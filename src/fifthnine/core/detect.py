"""Stack and version detection (M1).

Reads build descriptors, lockfiles, Dockerfiles and Kubernetes YAML to identify which supported components a
repository uses and at which versions. Values come from parsers. Line numbers come from the parser where it
provides them (XML, YAML, requirements files read line by line); for TOML, which gives no positions, from a search
narrowed to the right table and skipping comments. Nothing here evaluates reliability rules.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from fifthnine.core.safeparse import (
    ParseError,
    XmlNode,
    compose_yaml_documents,
    find_line,
    load_toml,
    mapping_child,
    mapping_scalars,
    parse_xml,
    section_start,
    toml_array_table_lines,
)
from fifthnine.core.workspace import (
    KIND_DOCKERFILE,
    KIND_GRADLE_BUILD,
    KIND_GRADLE_CATALOG,
    KIND_HELM_CHART,
    KIND_JAVA_SOURCE,
    KIND_KOTLIN_SOURCE,
    KIND_KUSTOMIZATION,
    KIND_LOCKFILE,
    KIND_MAVEN_POM,
    KIND_PYPROJECT,
    KIND_PYTHON_SOURCE,
    KIND_PYTHON_VERSION,
    KIND_REQUIREMENTS,
    KIND_SPRING_CONFIG,
    KIND_YAML,
    FileEntry,
    Workspace,
)
from fifthnine.schema.v1 import Fact, FactStatus, SkippedFile, SourceRef, Subject

EXTRACTOR_VERSION = "m1.1"
PYTHON_COMPONENTS = ("fastapi", "tenacity", "httpx", "gunicorn", "uvicorn")
K8S_WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod", "Rollout"})
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
K8S_HINT = re.compile(r"^apiVersion\s*:", re.MULTILINE)
K8S_KIND_HINT = re.compile(r"^kind\s*:", re.MULTILINE)


@dataclass
class Detection:
    component: str
    version: str | None
    source: SourceRef | None
    how: str
    note: str | None = None


@dataclass
class K8sObject:
    api_version: str
    kind: str
    name: str | None
    namespace: str | None
    source: SourceRef


@dataclass
class DetectionResult:
    detections: list[Detection] = field(default_factory=list)
    k8s_objects: list[K8sObject] = field(default_factory=list)
    helm_charts: list[str] = field(default_factory=list)
    helm_templates: list[str] = field(default_factory=list)
    kustomizations: list[str] = field(default_factory=list)
    spring_config_files: list[str] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)
    parsed: list[str] = field(default_factory=list)
    parse_errors: list[SkippedFile] = field(default_factory=list)
    unresolved: list[Detection] = field(default_factory=list)


def _ref(path: str, line: int | None) -> SourceRef:
    n = line or 1
    return SourceRef(path=path, line_start=n, line_end=n)


# ---------------------------------------------------------------- Maven


def _maven(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    root = parse_xml(text)
    props: dict[str, tuple[str, int]] = {}
    properties = root.child("properties")
    if properties is not None:
        for p in properties.children:
            props[p.tag] = (p.text.strip(), p.line)

    def resolve(node: XmlNode) -> tuple[str | None, int, str | None]:
        """(value, line, note). Resolves one level of ${property} from the same POM."""
        raw = node.text.strip()
        match = PLACEHOLDER.fullmatch(raw)
        if not match:
            return raw, node.line, None
        key = match.group(1)
        if key in props and not PLACEHOLDER.search(props[key][0]):
            return props[key][0], props[key][1], f"resolved from property ${{{key}}}"
        return None, node.line, f"unresolved property ${{{key}}} (not defined in this POM)"

    def add(component: str, node: XmlNode | None, how: str) -> None:
        if node is None:
            return
        value, line, note = resolve(node)
        out.detections.append(Detection(component, value, _ref(entry.path, line), how, note=note))

    parent = root.child("parent")
    if parent is not None and parent.child_text("groupId") == "org.springframework.boot" and parent.child_text("artifactId") in (
        "spring-boot-starter-parent", "spring-boot-dependencies",
    ):
        add("spring-boot", parent.child("version"), "maven parent " + (parent.child_text("artifactId") or ""))

    dm = root.child("dependencyManagement")
    dm_deps = dm.child("dependencies") if dm is not None else None
    for dep in dm_deps.children_named("dependency") if dm_deps is not None else []:
        if dep.child_text("groupId") == "org.springframework.boot" and dep.child_text("artifactId") == "spring-boot-dependencies":
            add("spring-boot", dep.child("version"), "maven BOM import spring-boot-dependencies")

    deps = root.child("dependencies")
    for dep in deps.children_named("dependency") if deps is not None else []:
        group = dep.child_text("groupId") or ""
        if group == "io.github.resilience4j":
            add("resilience4j", dep.child("version"), f"maven dependency {dep.child_text('artifactId')}")
        if group == "org.springframework":
            add("spring-framework", dep.child("version"), f"maven dependency {dep.child_text('artifactId')}")

    for key in ("java.version", "maven.compiler.release", "maven.compiler.source"):
        if key in props:
            value, line = props[key]
            unresolved = bool(PLACEHOLDER.search(value))
            out.detections.append(Detection("java", None if unresolved else _java_major(value), _ref(entry.path, line), f"maven property {key}",
                                            note=f"unresolved value {value}" if unresolved else None))
            break
    if "spring-framework.version" in props:
        value, line = props["spring-framework.version"]
        out.detections.append(Detection("spring-framework", value, _ref(entry.path, line), "maven property spring-framework.version"))
    return True


def _java_major(value: str) -> str:
    """'1.8' and 'VERSION_1_8' style values name Java 8; '17' and '17.0.2' are kept."""
    match = re.fullmatch(r"1[._](\d+)", value)
    return match.group(1) if match else value


# ---------------------------------------------------------------- Gradle

GRADLE_BOOT_PLUGIN = re.compile(r"""id\s*\(?\s*["']org\.springframework\.boot["']\s*\)?\s*\.?\s*version\s*\(?\s*["']([^"'$]+)["']""")
GRADLE_BOOT_CLASSPATH = re.compile(r"""["']org\.springframework\.boot:spring-boot-gradle-plugin:([^"'$]+)["']""")
GRADLE_JAVA = [
    re.compile(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)"),
    re.compile(r"jvmToolchain\(\s*(\d+)\s*\)"),
    re.compile(r"""(?:sourceCompatibility|targetCompatibility)\s*=\s*(?:JavaVersion\.VERSION_)?["']?(\d+(?:[._]\d+)?)"""),
]


def strip_jvm_comments(text: str) -> str:
    """Replace // and /* */ comments with spaces, keeping newlines so line numbers stay valid. String literals
    (single, double and triple quoted) are left intact, so URLs such as "https://repo" survive."""
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote:
            if text.startswith(quote, i):
                out.append(quote)
                i += len(quote)
                quote = None
                continue
            if c == "\\" and len(quote) == 1 and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            out.append(c)
            i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i:i + 3]
            out.append(quote)
            i += 3
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", text[i:end]))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _gradle(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    """Literal-only extraction from Groovy or Kotlin DSL with comments removed. Computed values are reported
    as not determined."""
    code = strip_jvm_comments(text)
    found = False
    for pattern, how in ((GRADLE_BOOT_PLUGIN, "gradle plugin literal"), (GRADLE_BOOT_CLASSPATH, "gradle buildscript classpath literal")):
        for match in pattern.finditer(code):
            out.detections.append(Detection("spring-boot", match.group(1), _ref(entry.path, _line_of(code, match.start())), how))
            found = True
    if not found and "org.springframework.boot" in code:
        out.unresolved.append(Detection("spring-boot", None, _ref(entry.path, _line_of(code, code.index("org.springframework.boot"))),
                                        "gradle build file",
                                        note="Spring Boot plugin referenced without a literal version (catalog alias, variable or settings plugin management)"))
    for java_pattern in GRADLE_JAVA:
        java_match = java_pattern.search(code)
        if java_match:
            out.detections.append(Detection("java", _java_major(java_match.group(1)), _ref(entry.path, _line_of(code, java_match.start())),
                                            "gradle java toolchain/compatibility literal"))
            break
    return True


def _gradle_catalog(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    data = load_toml(text)
    versions = data.get("versions", {}) or {}
    versions_at = section_start(text, "[versions]") or 1
    plugins_at = section_start(text, "[plugins]") or 1
    for alias, plugin in (data.get("plugins", {}) or {}).items():
        if not isinstance(plugin, dict) or plugin.get("id") != "org.springframework.boot":
            continue
        version = plugin.get("version")
        line: int | None
        if isinstance(version, dict) and "ref" in version:
            ref = str(version["ref"])
            version = versions.get(ref)
            line = _toml_key_line(text, ref, versions_at)
        else:
            line = _toml_key_line(text, str(alias), plugins_at)
        if isinstance(version, str):
            out.detections.append(Detection("spring-boot", version, _ref(entry.path, line), "gradle version catalog plugin"))
    return True


def _toml_key_line(text: str, key: str, start: int) -> int | None:
    pattern = re.compile(rf'^\s*"?{re.escape(key)}"?\s*=')
    for number, line in enumerate(text.splitlines(), 1):
        if number < start:
            continue
        if number > start and line.strip().startswith("["):
            return None
        if pattern.match(line):
            return number
    return None


# ---------------------------------------------------------------- Python


def _requirement(spec: str) -> tuple[str, str | None, str] | None:
    """(canonical name, exact version or None, specifier text) for tracked components; None for others.
    Raises InvalidRequirement for unparseable text."""
    req = Requirement(spec)
    name = canonicalize_name(req.name)
    if name not in PYTHON_COMPONENTS:
        return None
    pins = [s for s in req.specifier if s.operator in ("==", "===") and "*" not in s.version]
    exact = pins[0].version if len(pins) == 1 and len(req.specifier) == 1 else None
    return name, exact, str(req.specifier) or "(no version constraint)"


def _mentions_tracked(text: str) -> str | None:
    head = re.split(r"[\s\[<>=!~;@]", text.strip(), maxsplit=1)[0]
    name = canonicalize_name(head) if head else ""
    return name if name in PYTHON_COMPONENTS else None


def _add_requirement(entry: FileEntry, spec: str, line: int | None, how: str, out: DetectionResult) -> None:
    try:
        parsed = _requirement(spec)
    except InvalidRequirement:
        tracked = _mentions_tracked(spec)
        if tracked:
            out.unresolved.append(Detection(tracked, None, _ref(entry.path, line), how, note="requirement line could not be parsed"))
        return
    if parsed is None:
        return
    name, exact, specifier = parsed
    out.detections.append(Detection(name, exact, _ref(entry.path, line), how, note=None if exact else f"declared as {specifier}; no exact version"))


def _quoted_line(text: str, spec: str, start: int) -> int | None:
    return find_line(text, f'"{spec}"', start) or find_line(text, f"'{spec}'", start)


def _pyproject(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    data = load_toml(text)
    project = data.get("project", {}) or {}
    project_at = section_start(text, "[project]") or 1
    for spec in project.get("dependencies", []) or []:
        if isinstance(spec, str):
            _add_requirement(entry, spec, _quoted_line(text, spec, project_at), "pyproject [project].dependencies", out)
    for group, specs in (project.get("optional-dependencies", {}) or {}).items():
        for spec in specs or []:
            if isinstance(spec, str):
                _add_requirement(entry, spec, _quoted_line(text, spec, project_at), f"pyproject optional-dependencies.{group}", out)
    poetry = ((data.get("tool", {}) or {}).get("poetry", {}) or {}).get("dependencies", {}) or {}
    poetry_at = section_start(text, "[tool.poetry.dependencies]") or 1
    for name, constraint in poetry.items():
        cname = canonicalize_name(str(name))
        if cname not in PYTHON_COMPONENTS:
            continue
        value = constraint.get("version") if isinstance(constraint, dict) else constraint
        exact = value if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)*", value) else None
        out.detections.append(Detection(cname, exact, _ref(entry.path, _toml_key_line(text, str(name), poetry_at)),
                                        "pyproject [tool.poetry.dependencies]",
                                        note=None if exact else f"declared as {value!r}; no exact version"))
    return True


def _requirements_txt(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    """pip requirements format: continuation lines joined, comments and per-requirement options (--hash, ...)
    removed, line number of the requirement's first physical line kept."""
    logical: list[tuple[int, str]] = []
    buffer, first = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not buffer:
            first = number
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        if buffer.strip():
            logical.append((first, buffer.strip()))
        buffer = ""
    if buffer.strip():
        logical.append((first, buffer.strip()))
    for number, line in logical:
        if line.startswith("-"):
            continue  # -r, -c, -e, --index-url ... are not requirements
        spec = re.split(r"\s+--?[a-zA-Z]", " " + line, maxsplit=1)[0].strip()
        if spec:
            _add_requirement(entry, spec, number, "requirements file", out)
    return True


def _lockfile(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    data = load_toml(text)
    lines = toml_array_table_lines(text, "package", "name")
    for pkg in data.get("package", []) or []:
        if not isinstance(pkg, dict):
            continue
        raw_name = str(pkg.get("name", ""))
        name = canonicalize_name(raw_name)
        version = pkg.get("version")
        if name in PYTHON_COMPONENTS and isinstance(version, str):
            out.detections.append(Detection(name, version, _ref(entry.path, lines.get(raw_name)), f"lockfile {posixpath.basename(entry.path)}"))
    return True


def _python_version_file(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")), "")
    match = re.fullmatch(r"(?:python-?)?(\d+\.\d+)(?:\.\d+)?", first)
    if match:
        out.detections.append(Detection("python", match.group(1), _ref(entry.path, 1), ".python-version"))
    elif first:
        out.unresolved.append(Detection("python", None, _ref(entry.path, 1), ".python-version", note=f"unrecognised value {first[:40]!r}"))
    return True


# ---------------------------------------------------------------- Dockerfile

DOCKER_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
PY_IMAGE = re.compile(r"^(?:[\w.-]+(?::\d+)?/)*python:(\d+)(?:\.(\d+))?(?:[.\-].*)?$")
JAVA_IMAGE = re.compile(
    r"^(?:[\w.-]+(?::\d+)?/)*(?:eclipse-temurin|openjdk|amazoncorretto|azul/zulu-openjdk|bellsoft/liberica-openjdk-\w+|openjdk/jdk)"
    r":(\d+)(?:[.\-u].*)?$"
)


def _dockerfile(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    for number, line in enumerate(text.splitlines(), 1):
        match = DOCKER_FROM.match(line)
        if not match:
            continue
        image = match.group(1).split("@", 1)[0]  # a digest pins bytes, the tag still names the version
        if "$" in image:
            out.unresolved.append(Detection("container-base-image", None, _ref(entry.path, number), "Dockerfile FROM",
                                            note=f"base image {image} depends on a build argument"))
            continue
        py = PY_IMAGE.match(image)
        if py:
            if py.group(2) is None:
                out.unresolved.append(Detection("python", None, _ref(entry.path, number), f"Dockerfile FROM {image}",
                                                note=f"tag {image} does not pin a minor version"))
            else:
                out.detections.append(Detection("python", f"{py.group(1)}.{py.group(2)}", _ref(entry.path, number), f"Dockerfile FROM {image}"))
        java = JAVA_IMAGE.match(image)
        if java:
            out.detections.append(Detection("java", java.group(1), _ref(entry.path, number), f"Dockerfile FROM {image}"))
    return True


# ---------------------------------------------------------------- Kubernetes and Helm


def is_helm_template(path: str, text: str) -> bool:
    """A Helm template, which is not YAML until rendered. A `{{TOKEN}}` placeholder inside a quoted value of an
    otherwise plain manifest does not make it one."""
    return "{{" in text and ("/templates/" in f"/{path}" or ".Values" in text or "{{-" in text)


def _yaml(entry: FileEntry, text: str, out: DetectionResult) -> bool:
    """Inventory Kubernetes objects. Returns False (not parsed) for Helm templates and for YAML without top-level
    apiVersion and kind lines, which are therefore never fed to the parser."""
    if is_helm_template(entry.path, text):
        out.helm_templates.append(entry.path)
        return False
    if not (K8S_HINT.search(text) and K8S_KIND_HINT.search(text)):
        return False
    for node in compose_yaml_documents(text):
        top = mapping_scalars(node, "apiVersion", "kind")
        if "apiVersion" not in top or "kind" not in top:
            continue
        metadata = mapping_child(node, "metadata")
        meta = mapping_scalars(metadata, "name", "namespace") if metadata is not None else {}
        out.k8s_objects.append(K8sObject(top["apiVersion"][0], top["kind"][0], meta.get("name", (None, 0))[0],
                                         meta.get("namespace", (None, 0))[0], _ref(entry.path, top["kind"][1])))
    return True


# ---------------------------------------------------------------- driver

PARSERS: dict[str, Callable[[FileEntry, str, DetectionResult], bool]] = {
    KIND_MAVEN_POM: _maven,
    KIND_GRADLE_BUILD: _gradle,
    KIND_GRADLE_CATALOG: _gradle_catalog,
    KIND_PYPROJECT: _pyproject,
    KIND_REQUIREMENTS: _requirements_txt,
    KIND_LOCKFILE: _lockfile,
    KIND_PYTHON_VERSION: _python_version_file,
    KIND_DOCKERFILE: _dockerfile,
    KIND_YAML: _yaml,
}


def detect(ws: Workspace) -> DetectionResult:
    out = DetectionResult()
    for entry in ws.files:
        if entry.kind in PARSERS:
            try:
                text = ws.read_text(entry)
            except OSError as exc:
                out.parse_errors.append(SkippedFile(path=entry.path, reason="unsupported", detail=f"not readable: {exc.strerror}"))
                continue
            try:
                if PARSERS[entry.kind](entry, text, out):
                    out.parsed.append(entry.path)
            except ParseError as exc:
                out.parse_errors.append(SkippedFile(path=entry.path, reason="parse_error", detail=str(exc)))
            except (RecursionError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                out.parse_errors.append(SkippedFile(path=entry.path, reason="parse_error", detail=f"unexpected content ({type(exc).__name__})"))
        elif entry.kind == KIND_SPRING_CONFIG:
            out.spring_config_files.append(entry.path)
        elif entry.kind == KIND_HELM_CHART:
            out.helm_charts.append(entry.path)
        elif entry.kind == KIND_KUSTOMIZATION:
            out.kustomizations.append(entry.path)
        if entry.kind in (KIND_JAVA_SOURCE, KIND_KOTLIN_SOURCE, KIND_PYTHON_SOURCE):
            out.source_counts[entry.kind] = out.source_counts.get(entry.kind, 0) + 1
    out.detections = _dedupe(out.detections)
    return out


def _scope(d: Detection) -> str:
    return posixpath.dirname(d.source.path) if d.source else ""


def _dedupe(detections: Iterable[Detection]) -> list[Detection]:
    """Keep every distinct exact version. Drop a version-less entry only when the same directory also yields an
    exact version for that component (a pin or lockfile next to a range). Repeats of the same (component, version)
    keep one source, preferring lockfiles, then build descriptors, then Dockerfiles."""
    def priority(d: Detection) -> int:
        return 0 if d.how.startswith("lockfile") else 2 if d.how.startswith("Dockerfile") else 1

    items = sorted(detections, key=lambda d: (d.component, priority(d), d.source.path if d.source else "",
                                               d.source.line_start if d.source else 0))
    exact_scopes = {(d.component, _scope(d)) for d in items if d.version is not None}
    seen: set[tuple[str, str]] = set()
    kept: list[Detection] = []
    for d in items:
        if d.version is None:
            if (d.component, _scope(d)) not in exact_scopes:
                kept.append(d)
            continue
        if (d.component, d.version) in seen:
            continue
        seen.add((d.component, d.version))
        kept.append(d)
    return sorted(kept, key=lambda d: (d.component, d.version or "", d.source.path if d.source else "", d.source.line_start if d.source else 0))


def _fact_id(*parts: object) -> str:
    return "f-" + hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


def detection_facts(result: DetectionResult) -> list[Fact]:
    facts: list[Fact] = []
    for d in result.detections + result.unresolved:
        path = d.source.path if d.source else "-"
        line = d.source.line_start if d.source else 0
        key = f"{d.component}:{path}:{line}:{d.how}"
        facts.append(Fact(
            fact_id=_fact_id(d.component, path, line, d.how, d.version),
            kind=f"stack.{d.component}.version",
            subject=Subject(kind="stack.component", key=key, display=d.component),
            value=d.version,
            status=FactStatus.DECLARED if d.version is not None else FactStatus.UNRESOLVED,
            source=d.source,
            unresolved_reason=d.note if d.version is None else None,
            scope={"how": d.how} | ({"note": d.note} if d.note and d.version is not None else {}),
            extractor=f"stack-detect-{EXTRACTOR_VERSION}",
        ))
    for obj in result.k8s_objects:
        key = f"k8s:{obj.api_version}/{obj.kind}/{obj.namespace or '-'}/{obj.name or '-'}:{obj.source.path}:{obj.source.line_start}"
        facts.append(Fact(
            fact_id=_fact_id(key), kind="k8s.object",
            subject=Subject(kind="k8s.object", key=key, display=f"{obj.kind}/{obj.name or '?'}"),
            value={"apiVersion": obj.api_version, "kind": obj.kind, "name": obj.name, "namespace": obj.namespace},
            status=FactStatus.DECLARED, source=obj.source, extractor=f"k8s-inventory-{EXTRACTOR_VERSION}",
        ))
    facts.sort(key=lambda f: (f.kind, f.subject.key, f.fact_id))
    unique: list[Fact] = []
    for f in facts:
        if not unique or unique[-1].fact_id != f.fact_id:
            unique.append(f)
    return unique
