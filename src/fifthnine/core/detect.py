"""Stack and version detection (M1).

Reads build descriptors, lockfiles, Dockerfiles and Kubernetes YAML to identify which supported components a
repository uses and at which versions. Values come from parsers; line numbers come from the parser where it
provides them (XML, YAML) and otherwise from an exact-text search for the parsed value (TOML, requirements).
Nothing here evaluates reliability rules.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from fifthnine.core.safeparse import ParseError, XmlNode, find_line, load_toml, load_yaml_documents, parse_xml, yaml_key_line
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

EXTRACTOR_VERSION = "m1"
PYTHON_COMPONENTS = ("fastapi", "tenacity", "httpx", "gunicorn", "uvicorn")
K8S_WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod", "Rollout"})
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


@dataclass
class Detection:
    component: str
    version: str | None
    source: SourceRef | None
    how: str
    exact: bool = True
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


def _maven(entry: FileEntry, text: str, out: DetectionResult) -> None:
    root = parse_xml(text)
    props: dict[str, tuple[str, int]] = {}
    properties = root.child("properties")
    if properties is not None:
        for p in properties.children:
            props[p.tag] = (p.text.strip(), p.line)

    def resolve(node: XmlNode | None) -> tuple[str | None, int | None, str | None]:
        """Return (value, line, note). Resolves one level of ${property} from the same POM."""
        if node is None:
            return None, None, None
        raw = node.text.strip()
        match = PLACEHOLDER.fullmatch(raw)
        if not match:
            return raw, node.line, None
        key = match.group(1)
        if key in props and not PLACEHOLDER.search(props[key][0]):
            return props[key][0], props[key][1], f"resolved from property ${{{key}}}"
        return None, node.line, f"unresolved property ${{{key}}} (not defined in this POM)"

    def add(component: str, node: XmlNode | None, how: str) -> None:
        value, line, note = resolve(node)
        if value is None and node is None:
            return
        out.detections.append(Detection(component, value, _ref(entry.path, line), how, exact=value is not None, note=note))

    parent = root.child("parent")
    if parent is not None and parent.child_text("groupId") == "org.springframework.boot" and parent.child_text("artifactId") in (
        "spring-boot-starter-parent", "spring-boot-dependencies",
    ):
        add("spring-boot", parent.child("version"), "maven parent " + (parent.child_text("artifactId") or ""))

    dm = root.child("dependencyManagement")
    if dm is not None and dm.child("dependencies") is not None:
        for dep in dm.child("dependencies").children_named("dependency"):  # type: ignore[union-attr]
            if dep.child_text("groupId") == "org.springframework.boot" and dep.child_text("artifactId") == "spring-boot-dependencies":
                add("spring-boot", dep.child("version"), "maven BOM import spring-boot-dependencies")

    deps = root.child("dependencies")
    for dep in deps.children_named("dependency") if deps is not None else []:
        group = dep.child_text("groupId") or ""
        if group == "io.github.resilience4j" and dep.child("version") is not None:
            add("resilience4j", dep.child("version"), f"maven dependency {dep.child_text('artifactId')}")
        if group == "org.springframework" and dep.child("version") is not None:
            add("spring-framework", dep.child("version"), f"maven dependency {dep.child_text('artifactId')}")

    for key in ("java.version", "maven.compiler.release", "maven.compiler.source"):
        if key in props:
            value, line = props[key]
            out.detections.append(Detection("java", value if not PLACEHOLDER.search(value) else None, _ref(entry.path, line),
                                            f"maven property {key}", exact=not PLACEHOLDER.search(value)))
            break
    if "spring-framework.version" in props:
        value, line = props["spring-framework.version"]
        out.detections.append(Detection("spring-framework", value, _ref(entry.path, line), "maven property spring-framework.version"))


# ---------------------------------------------------------------- Gradle

GRADLE_BOOT_PLUGIN = re.compile(r"""id\s*\(?\s*["']org\.springframework\.boot["']\s*\)?\s*version\s*\(?\s*["']([^"'$]+)["']""")
GRADLE_BOOT_CLASSPATH = re.compile(r"""["']org\.springframework\.boot:spring-boot-gradle-plugin:([^"'$]+)["']""")
GRADLE_JAVA = [
    re.compile(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)"),
    re.compile(r"""(?:sourceCompatibility|targetCompatibility)\s*=\s*(?:JavaVersion\.VERSION_|["'])?(\d+)"""),
]


def _gradle(entry: FileEntry, text: str, out: DetectionResult) -> None:
    """Literal-only extraction. Groovy/Kotlin DSL is not evaluated; anything computed is reported unresolved."""
    found = False
    for pattern, how in ((GRADLE_BOOT_PLUGIN, "gradle plugin literal"), (GRADLE_BOOT_CLASSPATH, "gradle buildscript classpath literal")):
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            out.detections.append(Detection("spring-boot", match.group(1), _ref(entry.path, line), how))
            found = True
    if not found and "org.springframework.boot" in text:
        ref_line = find_line(text, "org.springframework.boot")
        out.unresolved.append(Detection("spring-boot", None, _ref(entry.path, ref_line), "gradle build file", exact=False,
                                        note="Spring Boot plugin referenced without a literal version (catalog alias, variable or settings plugin management)"))
    for java_pattern in GRADLE_JAVA:
        java_match = java_pattern.search(text)
        if java_match:
            java_line = text.count("\n", 0, java_match.start()) + 1
            out.detections.append(Detection("java", java_match.group(1), _ref(entry.path, java_line), "gradle java toolchain/compatibility literal"))
            break


def _gradle_catalog(entry: FileEntry, text: str, out: DetectionResult) -> None:
    data = load_toml(text)
    versions = data.get("versions", {}) or {}
    for _alias, plugin in (data.get("plugins", {}) or {}).items():
        if not isinstance(plugin, dict) or plugin.get("id") != "org.springframework.boot":
            continue
        version = plugin.get("version")
        if isinstance(version, dict) and "ref" in version:
            version = versions.get(version["ref"])
        if isinstance(version, str):
            out.detections.append(Detection("spring-boot", version, _ref(entry.path, find_line(text, f'"{version}"')), "gradle version catalog plugin"))


# ---------------------------------------------------------------- Python


def _requirement(spec: str) -> tuple[str, str | None, str] | None:
    """(canonical name, exact version or None, specifier text) for tracked components."""
    try:
        req = Requirement(spec)
    except InvalidRequirement:
        return None
    name = canonicalize_name(req.name)
    if name not in PYTHON_COMPONENTS:
        return None
    pins = [s for s in req.specifier if s.operator in ("==", "===") and "*" not in s.version]
    exact = pins[0].version if len(pins) == 1 and len(req.specifier) == 1 else None
    return name, exact, str(req.specifier) or "(no version constraint)"


def _add_requirement(entry: FileEntry, text: str, spec: str, how: str, out: DetectionResult) -> None:
    parsed = _requirement(spec)
    if parsed is None:
        return
    name, exact, specifier = parsed
    line = find_line(text, spec)
    note = None if exact else f"declared as {specifier}; no exact version"
    out.detections.append(Detection(name, exact, _ref(entry.path, line), how, exact=exact is not None, note=note))


def _pyproject(entry: FileEntry, text: str, out: DetectionResult) -> None:
    data = load_toml(text)
    project = data.get("project", {}) or {}
    for spec in project.get("dependencies", []) or []:
        if isinstance(spec, str):
            _add_requirement(entry, text, spec, "pyproject [project].dependencies", out)
    for group, specs in (project.get("optional-dependencies", {}) or {}).items():
        for spec in specs or []:
            if isinstance(spec, str):
                _add_requirement(entry, text, spec, f"pyproject optional-dependencies.{group}", out)
    poetry = ((data.get("tool", {}) or {}).get("poetry", {}) or {}).get("dependencies", {}) or {}
    for name, constraint in poetry.items():
        cname = canonicalize_name(str(name))
        if cname not in PYTHON_COMPONENTS:
            continue
        value = constraint.get("version") if isinstance(constraint, dict) else constraint
        exact = value if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)*", value) else None
        out.detections.append(Detection(cname, exact, _ref(entry.path, find_line(text, str(name))), "pyproject [tool.poetry.dependencies]",
                                        exact=exact is not None, note=None if exact else f"declared as {value!r}; no exact version"))


def _requirements_txt(entry: FileEntry, text: str, out: DetectionResult) -> None:
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        _add_requirement(entry, text, line, "requirements file", out)


def _lockfile(entry: FileEntry, text: str, out: DetectionResult) -> None:
    data = load_toml(text)
    for pkg in data.get("package", []) or []:
        if not isinstance(pkg, dict):
            continue
        name = canonicalize_name(str(pkg.get("name", "")))
        version = pkg.get("version")
        if name in PYTHON_COMPONENTS and isinstance(version, str):
            line = find_line(text, f'name = "{pkg.get("name")}"')
            out.detections.append(Detection(name, version, _ref(entry.path, line), f"lockfile {entry.path.rsplit('/', 1)[-1]}"))


def _python_version_file(entry: FileEntry, text: str, out: DetectionResult) -> None:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")), "")
    match = re.fullmatch(r"(?:python-?)?(\d+\.\d+)(?:\.\d+)?", first)
    if match:
        out.detections.append(Detection("python", match.group(1), _ref(entry.path, 1), ".python-version"))


# ---------------------------------------------------------------- Dockerfile

DOCKER_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
PY_IMAGE = re.compile(r"^(?:[\w.-]+(?::\d+)?/)*python:(\d+\.\d+)(?:[.\-].*)?$")
JAVA_IMAGE = re.compile(
    r"^(?:[\w.-]+(?::\d+)?/)*(?:eclipse-temurin|openjdk|amazoncorretto|azul/zulu-openjdk|bellsoft/liberica-openjdk-\w+)"
    r":(\d+)(?:[.\-u].*)?$"
)


def _dockerfile(entry: FileEntry, text: str, out: DetectionResult) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        match = DOCKER_FROM.match(line)
        if not match:
            continue
        image = match.group(1)
        if "$" in image:
            out.unresolved.append(Detection("container-base-image", None, _ref(entry.path, number), "Dockerfile FROM", exact=False,
                                            note=f"base image {image} depends on a build argument"))
            continue
        py = PY_IMAGE.match(image)
        if py:
            out.detections.append(Detection("python", py.group(1), _ref(entry.path, number), f"Dockerfile FROM {image}"))
        java = JAVA_IMAGE.match(image)
        if java:
            out.detections.append(Detection("java", java.group(1), _ref(entry.path, number), f"Dockerfile FROM {image}"))


# ---------------------------------------------------------------- Kubernetes and Helm


def _yaml(entry: FileEntry, text: str, out: DetectionResult) -> None:
    if "{{" in text and ("/templates/" in f"/{entry.path}" or ".Values" in text or "{{-" in text):
        out.helm_templates.append(entry.path)
        return
    for doc in load_yaml_documents(text):
        data = doc.data
        if not isinstance(data, dict):
            continue
        api, kind = data.get("apiVersion"), data.get("kind")
        if not isinstance(api, str) or not isinstance(kind, str):
            continue
        raw_meta = data.get("metadata")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        line = yaml_key_line(data, "kind") or doc.start_line
        out.k8s_objects.append(K8sObject(api, kind, _str(meta.get("name")), _str(meta.get("namespace")), _ref(entry.path, line)))


def _str(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) else None


# ---------------------------------------------------------------- driver

PARSERS = {
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
                PARSERS[entry.kind](entry, ws.read_text(entry), out)
                out.parsed.append(entry.path)
            except ParseError as exc:
                out.parse_errors.append(SkippedFile(path=entry.path, reason="parse_error", detail=str(exc)))
            except (UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
                out.parse_errors.append(SkippedFile(path=entry.path, reason="parse_error", detail=f"{type(exc).__name__}: {exc}"[:200]))
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


def _dedupe(detections: Iterable[Detection]) -> list[Detection]:
    """Keep every distinct exact version. Drop version-less entries for a component that also has an exact
    version, and drop repeats of the same (component, version), preferring lockfiles, then build descriptors, then Dockerfiles."""
    def priority(d: Detection) -> int:
        return 0 if d.how.startswith("lockfile") else 2 if d.how.startswith("Dockerfile") else 1

    items = sorted(detections, key=lambda d: (d.component, priority(d), d.source.path if d.source else "",
                                               d.source.line_start if d.source else 0))
    exact_components = {d.component for d in items if d.version is not None}
    seen: set[tuple[str, str | None]] = set()
    kept: list[Detection] = []
    for d in items:
        if d.version is None and d.component in exact_components:
            continue
        key = (d.component, d.version)
        if d.version is not None and key in seen:
            continue
        seen.add(key)
        kept.append(d)
    return sorted(kept, key=lambda d: (d.component, d.version or "", d.source.path if d.source else ""))


def detection_facts(result: DetectionResult) -> list[Fact]:
    facts: list[Fact] = []
    for d in result.detections + result.unresolved:
        key = f"{d.component}:{d.source.path if d.source else '-'}:{d.source.line_start if d.source else 0}"
        facts.append(Fact(
            fact_id="f-" + hashlib.sha256(key.encode()).hexdigest()[:12],
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
        key = f"k8s:{obj.kind}/{obj.namespace or '-'}/{obj.name or '-'}:{obj.source.path}:{obj.source.line_start}"
        facts.append(Fact(
            fact_id="f-" + hashlib.sha256(key.encode()).hexdigest()[:12], kind="k8s.object",
            subject=Subject(kind="k8s.object", key=key, display=f"{obj.kind}/{obj.name or '?'}"),
            value={"apiVersion": obj.api_version, "kind": obj.kind, "name": obj.name, "namespace": obj.namespace},
            status=FactStatus.DECLARED, source=obj.source, extractor=f"k8s-inventory-{EXTRACTOR_VERSION}",
        ))
    return sorted(facts, key=lambda f: (f.kind, f.subject.key))
