"""Spring Boot effective configuration, profiles and versioned defaults (M2-2).

For every Spring Boot module found in the repository this resolver reads the packaged configuration files
(`src/main/resources/application*.{yml,yaml,properties}` and `config/`), decides which profiles are active, layers the
sources in Spring Boot's precedence order and reports each reliability-relevant property with its provenance:

    config files < OS environment < system properties (JAVA_TOOL_OPTIONS, -D) < SPRING_APPLICATION_JSON < command line

Facts rest on the repository only. A container that is linked to the module (an inferred link, M2-1) supplies the
environment, the command line and the Dockerfile ENV. Out-of-repository sources (a Secret attached through `envFrom`,
a `valueFrom` reference) cannot be read, so a value that one of them could replace carries `override_exposure` and the
objects that could do it. Defaults come from the versioned table (`data/defaults/spring-boot.v1.yaml`) and always cite
the upstream source line; a Boot version the table does not cover gets no default.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from fifthnine.core.detect import Detection, DetectionResult
from fifthnine.core.facts import FactBuilder, fact_id, loc, ref, via
from fifthnine.core.safeparse import ParseError
from fifthnine.core.workspace import KIND_SPRING_CONFIG, Workspace
from fifthnine.extract.spring_config import (
    ConfigDocument,
    ProfileExpressionError,
    evaluate_profile_expression,
    parse_config_file,
)
from fifthnine.resolve.defaults import SpringDefault, spring_default, spring_minor
from fifthnine.resolve.deployment import DeploymentResult, EnvModel
from fifthnine.resolve.springprops import (
    MAX_PLACEHOLDER_TEXT,
    PLACEHOLDER_DEPTH,
    SPECS,
    canon,
    convert,
    env_name,
    find_placeholders,
    is_relevant,
    redact,
    spec_type,
    split_index,
)
from fifthnine.schema.v1 import (
    CoverageNotice,
    ExposureSource,
    FactInput,
    FactStatus,
    InputRole,
    Linkage,
    OverrideExposure,
    SourceRef,
)

EXTRACTOR = "spring-m2.2"
R = InputRole

BUILD_FILES = frozenset({"pom.xml", "build.gradle", "build.gradle.kts"})
BUILD_COMPONENTS = frozenset({"spring-boot", "java", "resilience4j", "spring-framework"})
LOCATIONS = ("src/main/resources", "src/main/resources/config")  # classpath:/ then classpath:/config/ (low to high)
EXTENSIONS = ("yaml", "yml", "properties")  # low to high inside one location (.properties wins)
MAX_PROPERTY_FACTS = 1500
JSON_VARS = ("SPRING_APPLICATION_JSON",)
SYSPROP_VARS = ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS")
OVERRIDE_VARS = (*JSON_VARS, *SYSPROP_VARS)
IGNORED_JVM_VARS = ("JAVA_OPTS", "JAVA_OPTIONS")  # not read by the JVM or by Spring Boot

# keys reported with their cited default when no source sets them
DEFAULT_KEYS = (
    "server.port", "server.shutdown", "server.servlet.context-path", "spring.lifecycle.timeout-per-shutdown-phase",
    "management.server.port", "management.server.base-path", "management.endpoints.web.base-path",
    "management.endpoints.web.exposure.include", "management.endpoints.web.exposure.exclude",
    "management.endpoints.web.path-mapping.health", "management.endpoint.health.probes.add-additional-paths",
)
DEFAULT_ALIASES = {"management.endpoints.web.path-mapping.health": "management.endpoints.web.path-mapping.*"}

UNKNOWN_INPUT = "an input value is not known (the Spring Boot version is outside the defaults table, or a value is unresolved)"
ACCESS_ORDER = {"none": 0, "read-only": 1, "unrestricted": 2}


@dataclass
class SpringResult:
    facts: FactBuilder
    coverage: list[CoverageNotice] = field(default_factory=list)
    parsed: list[str] = field(default_factory=list)
    consumed: set[str] = field(default_factory=set)  # configuration files that belong to a Spring module


@dataclass
class Hit:
    """One source's value for one property."""

    key: str  # spelling at the source
    raw: Any  # scalar, or list of scalars for an indexed list
    layer: str  # file | docker_env | env | sysprop | json | args
    precedence: str
    profile: str  # scope label: "default" for base files, the profile (expression) for profile-specific sources
    source: tuple[str, int] | None
    status: FactStatus = FactStatus.DECLARED
    via_fact: str | None = None  # fact that attaches the value (env entry, Dockerfile ENV)
    var: str | None = None  # environment variable that carried it
    profile_selected: bool = False  # reached only because a profile is active
    doc: ConfigDocument | None = None
    unresolved: str | None = None


@dataclass
class Prop:
    fid: str
    key: str
    value: Any
    unit: str | None
    status: FactStatus
    has_hit: bool
    layer: str
    source: tuple[str, int] | None
    exposure: OverrideExposure
    unresolved_reason: str | None = None
    default: SpringDefault | None = None


@dataclass
class Mat:
    """A hit with its placeholders resolved."""

    value: Any
    status: FactStatus
    source: tuple[str, int] | None
    inputs: list[FactInput]
    precedence: str
    placeholder_vars: list[str]
    unresolved: str | None = None


@dataclass
class ModuleInfo:
    root: str
    boot_version: str | None
    boot_detection: Detection | None
    detections: list[Detection]
    resources_dirs: list[str]
    kube_config: tuple[str, int] | None = None  # spring-cloud-kubernetes config dependency (path, line)

    @property
    def label(self) -> str:
        return self.root


def _dirname(path: str) -> str:
    return path.rpartition("/")[0]


def _find_modules(detection: DetectionResult) -> dict[str, ModuleInfo]:
    by_root: dict[str, list[Detection]] = {}
    for d in [*detection.detections, *detection.unresolved]:
        if d.source is None or d.component not in BUILD_COMPONENTS:
            continue
        path = d.source.path
        base = path.rpartition("/")[2]
        if base == "libs.versions.toml" and _dirname(path).endswith("gradle"):
            root = _dirname(_dirname(path))
        elif base in BUILD_FILES:
            root = _dirname(path)
        else:
            continue
        by_root.setdefault(root or ".", []).append(d)
    modules: dict[str, ModuleInfo] = {}
    for root, dets in by_root.items():
        boots = [d for d in dets if d.component == "spring-boot"]
        if not boots:
            continue
        boot = next((d for d in boots if d.version), boots[0])
        prefix = "" if root == "." else root + "/"
        modules[root] = ModuleInfo(root, boot.version, boot, dets, [prefix + loc_ for loc_ in LOCATIONS])
    return modules


def _is_access_model(version: str | None) -> bool | None:
    minor = spring_minor(version)
    if minor is None:
        return None
    major, _, minor_n = minor.partition(".")
    return (int(major), int(minor_n)) >= (3, 4)


def _clean_base(path: Any) -> str:
    text = str(path).strip()
    if text in ("", "/"):
        return ""
    text = text if text.startswith("/") else "/" + text
    return text.rstrip("/")


# ---------------------------------------------------------------- one resolution context (module x container)


class _Run:
    def __init__(self, ws: Workspace, mod: ModuleInfo, dep: DeploymentResult, fb: FactBuilder, cov: list[CoverageNotice],
                 subject_root: str, csub: str | None, link: Linkage | None, option: tuple[list[str], str] | None,
                 boot_fid: str | None, parsed: list[str], consumed: set[str], cache: dict[str, list[ConfigDocument] | None]) -> None:
        self.ws, self.mod, self.dep, self.fb, self.cov = ws, mod, dep, fb, cov
        self.subject_root = subject_root
        self.csub, self.link = csub, link
        self.option = option
        self.boot_fid = boot_fid
        self.version = mod.boot_version
        self.parsed, self.consumed, self.cache = parsed, consumed, cache
        self.model: EnvModel | None = dep.env_models.get(csub) if csub else None
        self.mounts = [f for f in dep.facts.facts() if f.kind == "k8s.container.volume_mount"] if csub else []
        self.link_ids = [link.link_id] if link is not None else []
        self.docker_env: dict[str, Any] = {}
        self.docker_path: str | None = None
        self.docker_stage_index = 0
        self._load_docker_env()
        self.files_table: dict[str, list[Hit]] = {}
        self.sys_table: dict[str, list[Hit]] = {}
        self.json_table: dict[str, list[Hit]] = {}
        self.args_table: dict[str, list[Hit]] = {}
        self.profiles: list[str] = []
        self.profile_unknown = False
        self.label = "default"
        self.active_fid: str | None = None
        self.props: dict[str, Prop | None] = {}
        self.included_docs: list[ConfigDocument] = []
        self.note_keys: set[str] = set()
        self.unmapped_env: set[str] = set()

    # -- inputs from the linked container

    def _load_docker_env(self) -> None:
        if self.link is None:
            return
        dpath = self.dep.dockerfile_for_link.get(self.link.link_id)
        if dpath is None:
            return
        model = self.dep.dockerfiles.get(dpath)
        target = model.default_target if model is not None else None
        if target is None:
            return
        self.docker_path, self.docker_stage_index = dpath, target.index
        self.docker_env = dict(target.env)

    def env_get(self, var: str) -> list[Hit]:
        """OS environment hits for `var`, highest first: the container's own environment, then the Dockerfile ENV."""
        out: list[Hit] = []
        if self.model is not None:
            res = self.model.resolve(var)
            if res is not None:
                out.append(Hit(var, res.value, "env", "spring.os_environment", "", res.source, res.status, res.via_fact, var,
                               unresolved=res.reason if res.status is FactStatus.UNRESOLVED else None))
        v = self.docker_env.get(var)
        if v is not None and self.docker_path is not None:
            fid = fact_id("docker.env", f"docker:{self.docker_path}#{self.docker_stage_index}#{var}", {})
            out.append(Hit(var, v.value, "docker_env", "spring.os_environment", "", (self.docker_path, v.line),
                           FactStatus.INFERRED if v.unresolved is None else FactStatus.UNRESOLVED, fid, var, unresolved=v.unresolved))
        return out

    # -- configuration files

    def _docs(self, path: str) -> list[ConfigDocument]:
        if path in self.cache:
            return self.cache[path] or []
        entry = next((f for f in self.ws.files if f.path == path), None)
        docs: list[ConfigDocument] | None = None
        if entry is not None:
            try:
                docs = parse_config_file(path, self.ws.read_text(entry))
                self.parsed.append(path)
            except (ParseError, OSError, ValueError, RecursionError) as exc:
                self.cov.append(CoverageNotice(code="FN-OPS-002", scope=path,
                                               message=f"Spring configuration file not read: {str(exc)[:200]}. Its values and any profile it "
                                                       "selects are unknown."))
        self.cache[path] = docs
        return docs or []

    def _file_candidates(self, stem: str) -> list[tuple[str, str]]:
        """(path, location) of `stem.<ext>` files, lowest precedence first."""
        known = {f.path for f in self.ws.files if f.kind == KIND_SPRING_CONFIG}
        out = []
        for location in self.mod.resources_dirs:
            for ext in EXTENSIONS:
                p = f"{location}/{stem}.{ext}"
                if p in known:
                    out.append((p, location))
        return out

    def _doc_hits(self, doc: ConfigDocument, precedence: str, label: str, selected: bool) -> dict[str, list[Hit]]:
        hits: dict[str, Hit] = {}
        lists: dict[str, list[tuple[int, Any, int]]] = {}
        spelled: dict[str, str] = {}
        for e in doc.entries:
            base, idx = split_index(e.key)
            c = canon(base)
            if idx is not None:
                lists.setdefault(c, []).append((idx, e.value, e.line))
                spelled.setdefault(c, base)
                continue
            hits[canon(e.key)] = Hit(e.key, e.value, "file", precedence, label, (doc.path, e.line), profile_selected=selected, doc=doc)
        for c, items in lists.items():
            items.sort()
            hits[c] = Hit(spelled[c], [v for _, v, _ in items], "file", precedence, label, (doc.path, items[0][2]),
                          profile_selected=selected, doc=doc)
        return {c: [h] for c, h in hits.items()}

    def _build_files(self, active: list[str] | None) -> None:
        """Layer the packaged files. `active=None` is the first pass that only reads unconditional base documents to learn
        the profiles; the second pass adds conditional documents and profile-specific files."""
        table: dict[str, list[Hit]] = {}
        included: list[ConfigDocument] = []
        profile_set = set(active) if active else {"default"}

        def add(docs: list[ConfigDocument], precedence: str, label: str, selected: bool, profile_file: bool) -> None:
            for doc in docs:
                if doc.legacy_profiles_selector is not None:
                    if active is not None:
                        v, ln = doc.legacy_profiles_selector
                        self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{doc.path}:{ln}",
                                                       message="spring.profiles is not accepted by Spring Boot 3 (use spring.config.activate.on-profile). "
                                                               "The application fails to start with this file; its values were not used."))
                    continue
                doc_label, doc_selected = label, selected
                if doc.other_conditions:
                    if active is not None:
                        key, _, ln = doc.other_conditions[0]
                        self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{doc.path}:{ln}",
                                                       message=f"Document condition {key} is not evaluated; the document's values were not used."))
                    continue
                if doc.on_profile is not None:
                    if profile_file:
                        if active is not None:
                            self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{doc.path}:{doc.on_profile_line}",
                                                           message="spring.config.activate.on-profile is not allowed in a profile-specific file; "
                                                                   "Spring Boot rejects it."))
                        continue
                    if active is None:
                        continue
                    try:
                        if not evaluate_profile_expression(doc.on_profile, profile_set):
                            continue
                    except ProfileExpressionError as exc:
                        self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{doc.path}:{doc.on_profile_line}",
                                                       message=f"spring.config.activate.on-profile {doc.on_profile!r} is not valid: {exc}. "
                                                               "The document was not used."))
                        continue
                    doc_label, doc_selected = doc.on_profile, True
                for key in ("spring.profiles.active", "spring.profiles.include"):
                    sets_it = any(canon(split_index(e.key)[0]) == canon(key) for e in doc.entries)
                    if profile_file and sets_it and active is not None:
                        self.cov.append(CoverageNotice(code="FN-OPS-002", scope=doc.path,
                                                       message=f"{key} is not allowed in a profile-specific file; Spring Boot rejects it. "
                                                               "The setting was ignored."))
                included.append(doc)
                for c, hs in self._doc_hits(doc, precedence, doc_label, doc_selected).items():
                    table.setdefault(c, []).extend(hs)

        for path, _ in self._file_candidates("application"):
            self.consumed.add(path)
            add(self._docs(path), "spring.packaged_application_file", "default", False, False)
        if active is not None:
            for p in active:
                for path, _ in self._file_candidates(f"application-{p}"):
                    self.consumed.add(path)
                    add(self._docs(path), f"spring.packaged_profile_file:{p}", p, True, True)
        self.files_table = table
        self.included_docs = included

    # -- command line, system properties, SPRING_APPLICATION_JSON

    def _tokens(self) -> list[tuple[str, str, int]]:
        if self.csub is None:
            return []
        return self.dep.container_args.get(self.csub, [])

    def _build_overrides(self) -> None:
        self.sys_table, self.json_table, self.args_table = {}, {}, {}
        # JAVA_TOOL_OPTIONS and JDK_JAVA_OPTIONS (lowest of the system properties)
        for var in SYSPROP_VARS:
            for hit in self.env_get(var)[:1]:
                if hit.status is FactStatus.UNRESOLVED or hit.raw is None:
                    self.cov.append(CoverageNotice(code="FN-OPS-002", scope=self._where(hit),
                                                   message=f"{var} cannot be read from the repository; system properties it sets are unknown."))
                    continue
                for word in self._words(str(hit.raw), hit):
                    self._add_d(word, hit.source, hit.status, hit.via_fact, var)
        for var in IGNORED_JVM_VARS:
            if self.env_get(var):
                hit = self.env_get(var)[0]
                self.cov.append(CoverageNotice(code="FN-OPS-002", scope=self._where(hit),
                                               message=f"{var} is not read by the JVM or by Spring Boot, so it is ignored; use JAVA_TOOL_OPTIONS."))
        for token, path, line in self._tokens():
            if "$(" in token:
                self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{path}:{line}",
                                               message="A command or args entry uses $(VAR) expansion; the kubelet resolves it at runtime, "
                                                   "so options in it are unknown."))
                continue
            for word in self._words(token, None):
                if word.startswith("-D"):
                    self._add_d(word, (path, line), FactStatus.DECLARED, None, None)
                elif word.startswith("--") and "=" in word:
                    k, _, v = word[2:].partition("=")
                    if re.fullmatch(r"[A-Za-z][\w.\-\[\]]*", k):
                        self.args_table.setdefault(canon(k), []).append(
                            Hit(k, v, "args", "spring.command_line", "", (path, line), FactStatus.DECLARED))
        for var in JSON_VARS:
            for hit in self.env_get(var)[:1]:
                if hit.status is FactStatus.UNRESOLVED or hit.raw is None:
                    self.cov.append(CoverageNotice(code="FN-OPS-002", scope=self._where(hit),
                                                   message=f"{var} cannot be read from the repository; properties it sets are unknown."))
                    continue
                flat = _flatten_json(str(hit.raw))
                if flat is None:
                    self.cov.append(CoverageNotice(code="FN-OPS-002", scope=self._where(hit),
                                                   message=f"{var} is not a JSON object the scanner can read; properties it sets are unknown."))
                    continue
                for k, v in flat:
                    base, idx = split_index(k)
                    self.json_table.setdefault(canon(k), []).append(
                        Hit(k, v, "json", "spring.application_json", "", hit.source, hit.status, hit.via_fact, var))

    def _words(self, text: str, hit: Hit | None) -> list[str]:
        if " " not in text.strip() and "\t" not in text:
            return [text.strip()]
        try:
            return shlex.split(text)
        except ValueError:
            self.cov.append(CoverageNotice(code="FN-OPS-002", scope=self._where(hit) if hit else "repository",
                                           message="A command line or JVM option string could not be split into words; options in it are unknown."))
            return []

    def _add_d(self, word: str, source: tuple[str, int] | None, status: FactStatus, via_fact: str | None, var: str | None) -> None:
        if not word.startswith("-D") or len(word) < 3:
            return
        k, _, v = word[2:].partition("=")
        if re.fullmatch(r"[A-Za-z][\w.\-\[\]]*", k):
            self.sys_table.setdefault(canon(k), []).append(Hit(k, v, "sysprop", "spring.system_property", "", source, status, via_fact, var))

    def _where(self, hit: Hit | None) -> str:
        if hit is not None and hit.source is not None:
            return f"{hit.source[0]}:{hit.source[1]}"
        return self.csub or "repository"

    # -- layered lookup

    def hits(self, key: str) -> list[Hit]:
        """Every source's value for `key`, highest precedence first."""
        c = canon(key)
        out: list[Hit] = []
        out += list(reversed(self.args_table.get(c, [])))
        out += list(reversed(self.json_table.get(c, [])))
        out += list(reversed(self.sys_table.get(c, [])))
        name = env_name(key)
        for h in self.env_get(name):
            out.append(Hit(key, h.raw, h.layer, h.precedence, h.profile, h.source, h.status, h.via_fact, name, unresolved=h.unresolved))
        out += list(reversed(self.files_table.get(c, [])))
        return out

    # -- placeholders

    def materialize(self, key: str, hit: Hit, depth: int = 0) -> Mat:
        raw = hit.raw
        if hit.unresolved is not None or (hit.status is FactStatus.UNRESOLVED):
            return Mat(None, FactStatus.UNRESOLVED, hit.source, [], hit.precedence, [], hit.unresolved or "the value cannot be read from the repository")
        if isinstance(raw, list):
            parts = [self.materialize(key, replace(hit, raw=v), depth) for v in raw]
            bad = next((p for p in parts if p.unresolved), None)
            if bad is not None:
                return Mat(None, FactStatus.UNRESOLVED, hit.source, [], hit.precedence, [], bad.unresolved)
            status = _worst([hit.status, *[p.status for p in parts]])
            return Mat([p.value for p in parts], status, hit.source, [i for p in parts for i in p.inputs], hit.precedence,
                       [v for p in parts for v in p.placeholder_vars])
        if not isinstance(raw, str) or "${" not in raw:
            return Mat(raw, hit.status, hit.source, [], hit.precedence, [])
        if depth > PLACEHOLDER_DEPTH or len(raw) > MAX_PLACEHOLDER_TEXT:
            return Mat(None, FactStatus.UNRESOLVED, hit.source, [], hit.precedence, [], "placeholders nest too deeply to resolve")
        text, status, inputs, pvars, reason, src, prec = self._substitute(raw, hit, depth)
        if reason is not None:
            return Mat(None, FactStatus.UNRESOLVED, hit.source, inputs, hit.precedence, pvars, reason)
        return Mat(text, status, src or hit.source, inputs, prec or hit.precedence, pvars)

    def _substitute(self, raw: str, hit: Hit, depth: int) -> tuple[str, FactStatus, list[FactInput], list[str], str | None, tuple[str, int] | None, str | None]:
        uses = find_placeholders(raw)
        if uses is None:
            return raw, hit.status, [], [], "a placeholder is not closed", None, None
        out: list[str] = []
        last = 0
        status = hit.status
        inputs: list[FactInput] = []
        pvars: list[str] = []
        src: tuple[str, int] | None = None
        prec: str | None = None
        for u in uses:
            out.append(raw[last:u.start])
            last = u.end
            name_mat = self._resolve_text(u.name, hit, depth + 1)
            if name_mat[1] is not None:
                return raw, status, inputs, pvars, name_mat[1], None, None
            name = name_mat[0]
            if hit.source is not None:
                inputs.append(loc(R.PLACEHOLDER, hit.source[0], hit.source[1]))
            pvars.append(name)
            found = self._lookup_name(name, hit, depth + 1)
            if found is not None:
                value, f_status, f_source, f_via, f_prec, f_reason = found
                if f_reason is not None:
                    return raw, status, inputs, pvars, f_reason, None, None
                out.append(_text(value))
                status = _worst([status, f_status])
                if f_via is not None:
                    inputs.append(via(R.ENV_ATTACHMENT, f_via))
                if len(uses) == 1 and raw == raw[u.start:u.end]:
                    src, prec = f_source, f"spring.placeholder:{_short(f_prec)}"
                continue
            if u.default is None:
                return raw, status, inputs, pvars, f"placeholder ${{{name}}} has no value in the repository and no default", None, None
            dmat = self._resolve_text(u.default, hit, depth + 1)
            if dmat[1] is not None:
                return raw, status, inputs, pvars, dmat[1], None, None
            out.append(dmat[0])
            if len(uses) == 1 and raw == raw[u.start:u.end]:
                prec = "spring.placeholder:default"
        out.append(raw[last:])
        return "".join(out), status, inputs, pvars, None, src, prec

    def _resolve_text(self, text: str, hit: Hit, depth: int) -> tuple[str, str | None]:
        if "${" not in text:
            return text, None
        if depth > PLACEHOLDER_DEPTH:
            return text, "placeholders nest too deeply to resolve"
        t, _s, _i, _p, reason, _src, _prec = self._substitute(text, hit, depth)
        return t, reason

    def _lookup_name(self, name: str, origin: Hit, depth: int) -> tuple[Any, FactStatus, tuple[str, int] | None, str | None, str, str | None] | None:
        """Value of placeholder `name` from the layered sources, or None when no source in the repository sets it."""
        c = canon(name)
        candidates: list[Hit] = []
        candidates += list(reversed(self.args_table.get(c, [])))
        candidates += list(reversed(self.json_table.get(c, [])))
        candidates += list(reversed(self.sys_table.get(c, [])))
        candidates += self.env_get(name)
        if env_name(name) != name:
            candidates += self.env_get(env_name(name))
        candidates += [h for h in reversed(self.files_table.get(c, [])) if h is not origin]
        if not candidates:
            return None
        top = candidates[0]
        mat = self.materialize(name, top, depth)
        if mat.unresolved is not None:
            return None, FactStatus.UNRESOLVED, top.source, top.via_fact, top.precedence, mat.unresolved
        via_fact = top.via_fact if top.layer in ("env", "docker_env") else None
        return mat.value, mat.status, top.source, via_fact, top.precedence, None

    # -- exposure

    def _file_sources(self) -> list[ExposureSource]:
        """Out-of-repository files that Spring reads before the packaged ones: a volume mounted where the application looks
        for `./config`, and `spring.config.import` targets."""
        out: list[ExposureSource] = []
        if self.csub is not None:
            ns = self.csub.split("/")[1] if self.csub.count("/") >= 2 else "_"
            for f in self.mounts:
                v = f.value
                if f.subject.key.startswith(self.csub + "#") and isinstance(v, dict) and v.get("config_location") is True \
                        and v.get("source_kind") in ("ConfigMap", "Secret", "PersistentVolumeClaim", "projected"):
                    out.append(ExposureSource(via="volume", object=f"k8s:{v['source_kind']}/{ns}/{v.get('source_name') or '?'}",
                                              variables=["application.yml", "application.properties"]))
        for var in ("SPRING_CONFIG_LOCATION", "SPRING_CONFIG_ADDITIONAL_LOCATION", "SPRING_CONFIG_IMPORT"):
            for h in self.env_get(var)[:1]:
                out.append(ExposureSource(via="volume", object=f"{var}:{h.raw if h.raw is not None else 'unresolved'}", variables=[]))
        if self.mod.kube_config is not None:
            out.append(ExposureSource(via="volume", object="spring-cloud-kubernetes-config:ConfigMap and Secret read from the Kubernetes API",
                                      variables=[]))
        for doc in self.included_docs:
            for value, _line in doc.imports:
                if not value.strip():
                    continue
                out.append(ExposureSource(via="volume", object=f"spring.config.import:{value}", variables=[]))
        return out

    def exposure(self, key: str, layer: str, placeholder_vars: Sequence[str] = (), var: str | None = None) -> tuple[OverrideExposure, list[ExposureSource]]:
        files = self._file_sources() if layer in ("file", "default") else []
        if self.model is None:
            return OverrideExposure.ENV_POSSIBLE, files  # no container is linked: what the environment sets is unknown
        names: list[str] = []
        if layer in ("file", "default"):
            names += [*placeholder_vars, env_name(key)]
        elif layer in ("env", "docker_env") and var is not None:
            names.append(var)
        if layer in ("file", "default", "env", "docker_env"):
            names += list(OVERRIDE_VARS)
        elif layer == "sysprop":
            names += list(JSON_VARS)
        merged: dict[tuple[str, str | None], ExposureSource] = {}
        for n in dict.fromkeys(names):
            for src in self.model.exposure_for(n):
                k = (src.via, src.object)
                if k in merged:
                    known = merged[k].variables
                    merged[k] = merged[k].model_copy(update={"variables": [*known, *[v for v in src.variables if v not in known]]})
                else:
                    merged[k] = src
            if n in OVERRIDE_VARS and n in self.model.explicit and self.model.explicit[n].status is FactStatus.UNRESOLVED:
                merged[("valueFrom", f"{self.csub}#{n}")] = ExposureSource(via="valueFrom", object=f"{self.csub}#{n}", variables=[n])
        env_srcs = list(merged.values())
        if env_srcs:
            return OverrideExposure.ENV_POSSIBLE, [*env_srcs, *files]
        if files:
            return OverrideExposure.FILE_POSSIBLE, files
        return OverrideExposure.NONE, []

    # -- profiles

    def _expand(self, names: list[str], groups: dict[str, list[str]]) -> list[str]:
        out: dict[str, None] = {}
        stack = list(reversed(names))
        steps = 0
        while stack and steps < 10_000:
            steps += 1
            cur = stack.pop()
            out[cur] = None
            stack.extend(reversed(groups.get(cur, [])))
        return list(out)

    def _groups(self) -> tuple[dict[str, list[str]], list[tuple[str, int]]]:
        groups: dict[str, list[str]] = {}
        locs: list[tuple[str, int]] = []
        prefix = canon("spring.profiles.group.")
        for c, hs in self.files_table.items():
            if not c.startswith(prefix):
                continue
            h = hs[-1]
            name = split_index(h.key)[0][len("spring.profiles.group."):]
            mat = self.materialize(h.key, h)
            if mat.unresolved is not None:
                continue
            value = mat.value if isinstance(mat.value, list) else [p.strip() for p in str(mat.value).split(",") if p.strip()]
            groups[name] = [str(v) for v in value]
            if h.source is not None:
                locs.append(h.source)
        return groups, locs

    def resolve_profiles(self) -> None:
        self._build_overrides()
        self._build_files(None)
        groups, group_locs = self._groups()
        include_names: list[str] = []
        include_locs: list[tuple[str, int]] = []
        for h in self.hits("spring.profiles.include")[:1]:
            m = self.materialize(h.key, h)
            if m.unresolved is None:
                include_names = [str(v) for v in (m.value if isinstance(m.value, list) else str(m.value).split(",")) if str(v).strip()]
                include_names = [n.strip() for n in include_names]
                if h.source is not None:
                    include_locs.append(h.source)
        active_names: list[str] = []
        top: Hit | None = None
        status = FactStatus.DECLARED
        unresolved: str | None = None
        precedence = "fifthnine.profile_option"
        env_conflict: Hit | None = None
        if self.option is not None:
            active_names = self.option[0]
            for h in self.hits("spring.profiles.active")[:1]:
                env_conflict = h
        else:
            for h in self.hits("spring.profiles.active")[:1]:
                top = h
                m = self.materialize(h.key, h)
                precedence = h.precedence
                status = m.status
                if m.unresolved is not None:
                    unresolved = m.unresolved
                else:
                    active_names = [p.strip() for p in (m.value if isinstance(m.value, list) else str(m.value).split(",")) if str(p).strip()]
        if env_conflict is not None and env_conflict.raw is not None:
            env_names = sorted({p.strip() for p in str(env_conflict.raw).split(",") if p.strip()})
            if env_names and env_names != sorted(set(active_names)):
                self.cov.append(CoverageNotice(
                    code="FN-OPS-002", scope=self._where(env_conflict),
                    message=f"The scan was told to assume profile(s) {', '.join(active_names)} ({self.option[1] if self.option else 'option'}), but the "
                            f"repository activates {', '.join(env_names)} here. The scan option was used."))
        if unresolved is not None:
            self.profile_unknown = True
            self.profiles = []
        else:
            self.profiles = self._expand([*include_names, *active_names], groups)
        self.label = ",".join(self.profiles) if self.profiles else "default"
        self._build_files(self.profiles if not self.profile_unknown else [])
        self._emit_active(top, status, unresolved, precedence, active_names, include_names, group_locs, include_locs)

    def _emit_active(self, top: Hit | None, status: FactStatus, unresolved: str | None, precedence: str, active_names: list[str],
                     include_names: list[str], group_locs: list[tuple[str, int]], include_locs: list[tuple[str, int]]) -> None:
        subject = f"spring:{self.subject_root}"
        inputs: list[FactInput] = []
        source: SourceRef | None = None
        layer = "file"
        expo_key = "spring.profiles.active"
        if self.option is not None:
            st, prec = FactStatus.DECLARED, "fifthnine.profile_option"
            layer = "option"
        elif top is not None:
            layer = top.layer
            prec = top.precedence
            st = status
            if top.source is not None:
                source = ref(*top.source)
            if top.via_fact is not None:
                inputs.append(via(R.PROFILE_ACTIVATION, top.via_fact))
        else:
            st, prec = FactStatus.DEFAULT, "spring.default_profile"
            anchor = self._primary_location()
            inputs.append(loc(R.ABSENCE, *anchor))
            layer = "default"
        for p, n in [*group_locs, *include_locs]:
            inputs.append(loc(R.VALUE_SOURCE, p, n))
        if unresolved is not None:
            expo, srcs = self.exposure(expo_key, layer, [], top.var if top else None)
            self.active_fid = self.fb.add("spring.active_profiles", subject, None, FactStatus.UNRESOLVED, source=source, inputs=inputs,
                                          exposure=expo, exposure_sources=srcs, link_ids=self.link_ids, unresolved_reason=unresolved)
            self.cov.append(CoverageNotice(code="FN-OPS-002", scope=subject,
                                           message=f"The active profile cannot be resolved ({unresolved}); profile-specific files were not applied "
                                                   "and values read from base files are marked inferred."))
            return
        expo, srcs = self.exposure(expo_key, layer, [], top.var if top else None)
        self.active_fid = self.fb.add("spring.active_profiles", subject, list(self.profiles), st, source=source, inputs=inputs,
                                      exposure=expo, exposure_sources=srcs, link_ids=self.link_ids, precedence=prec)
        if not self.profiles:
            prefixes = tuple(d + "/" for d in self.mod.resources_dirs)
            inactive = sorted(f.path for f in self.ws.files
                              if f.kind == KIND_SPRING_CONFIG and f.path.startswith(prefixes) and f.path.rpartition("/")[2].startswith("application-"))
            tail = f" Profile-specific files not applied: {', '.join(inactive)}." if inactive else ""
            self.cov.append(CoverageNotice(code="FN-OPS-002", scope=subject,
                                           message="No profile is activated in the repository, so Spring Boot uses the default profile." + tail
                                                   + (" An attached Secret or ConfigMap could still activate one." if srcs else "")))

    def _primary_location(self) -> tuple[str, int]:
        for doc in self.included_docs:
            return doc.path, 1
        b = self.mod.boot_detection
        if b is not None and b.source is not None:
            return b.source.path, b.source.line_start
        return ("pom.xml", 1)

    # -- property facts

    def scope(self) -> dict[str, str]:
        return {"profile": self.label}

    def prop(self, key: str, *, default: bool = True) -> Prop | None:
        c = canon(key)
        if c in self.props:
            return self.props[c]
        result = self._prop(key, default)
        if result is not None or default:
            self.props[c] = result
        return result

    def _status_for(self, st: FactStatus) -> FactStatus:
        if self.profile_unknown and st is FactStatus.DECLARED:
            return FactStatus.INFERRED
        return st

    def _prop(self, key: str, default: bool) -> Prop | None:
        hs = self.hits(key)
        subject = f"spring:{self.subject_root}:{key}"
        if hs:
            top = hs[0]
            display_key = key if (key in self._registry_names() or top.layer != "file") else top.key
            subject = f"spring:{self.subject_root}:{display_key}"
            return self._effective(subject, key, hs)
        if not default:
            return None
        return self._default_prop(subject, key)

    def _registry_names(self) -> set[str]:
        return set(DEFAULT_KEYS)

    def _effective(self, subject: str, key: str, hs: list[Hit]) -> Prop:
        top = hs[0]
        mat = self.materialize(key, top)
        kind = spec_type(key)
        scope = self.scope()
        expo, srcs = self.exposure(key, top.layer, mat.placeholder_vars, top.var)
        inputs = list(mat.inputs)
        if top.profile_selected and self.active_fid is not None:
            inputs.append(via(R.PROFILE_ACTIVATION, self.active_fid))
        source = ref(*mat.source) if mat.source is not None else None
        if mat.unresolved is not None:
            fid = self.fb.add("spring.property", subject, None, FactStatus.UNRESOLVED, source=source, scope=scope, inputs=inputs,
                              exposure=expo, exposure_sources=srcs, link_ids=self.link_ids, precedence=mat.precedence,
                              unresolved_reason=mat.unresolved)
            self._add_overridden(subject, key, hs[1:], fid)
            return Prop(fid, key, None, None, FactStatus.UNRESOLVED, True, top.layer, mat.source, expo, mat.unresolved)
        value, unit = convert(key, mat.value, kind)
        shown = redact(key, value)
        status = self._status_for(mat.status)
        fid = self.fb.add("spring.property", subject, shown, status, source=source, scope=scope, unit=unit, inputs=inputs,
                          exposure=expo, exposure_sources=srcs, link_ids=self.link_ids, precedence=mat.precedence)
        self._add_overridden(subject, key, hs[1:], fid)
        return Prop(fid, key, value, unit, status, True, top.layer, mat.source, expo)

    def _add_overridden(self, subject: str, key: str, rest: list[Hit], winner: str) -> None:
        for h in rest:
            mat = self.materialize(key, h)
            if mat.unresolved is not None:
                continue
            value, unit = convert(key, mat.value, spec_type(key))
            label = h.profile or self.label
            scope = {"profile": label}
            fid = fact_id("spring.property", subject, scope)
            if fid in self.fb._facts:  # same label as another fact of this property: tell them apart by origin
                if mat.source is None:
                    continue
                scope = {"profile": label, "origin": f"{mat.source[0]}:{mat.source[1]}"}
            self.fb.add("spring.property", subject, redact(key, value), FactStatus.OVERRIDDEN, source=ref(*mat.source) if mat.source else None,
                        scope=scope, unit=unit, link_ids=self.link_ids, precedence=h.precedence, overridden_by=winner)

    def _absence_input(self, key: str) -> FactInput:
        """The identity line that shows `key` is not set: the deepest existing parent key of `key` in the documents read,
        lowest precedence first on a tie."""
        parts = key.split(".")
        best: tuple[int, str, int] | None = None
        for doc in self.included_docs:
            lookup = {canon(p): line for p, line in doc.prefixes.items()}
            for depth in range(len(parts) - 1, 0, -1):
                line = lookup.get(canon(".".join(parts[:depth])))
                if line is not None:
                    if best is None or depth > best[0]:
                        best = (depth, doc.path, line)
                    break
        if best is not None:
            return loc(R.ABSENCE, best[1], best[2])
        return loc(R.ABSENCE, *self._primary_location())

    def _default_prop(self, subject: str, key: str) -> Prop | None:
        table_key = DEFAULT_ALIASES.get(key, key)
        d = spring_default(table_key, self.version)
        if d is None:
            return None
        expo, srcs = self.exposure(key, "default")
        inputs = [self._absence_input(key)]
        if self.boot_fid is not None:
            inputs.append(via(R.VERSION_BASIS, self.boot_fid))
        value = d.value
        fid = self.fb.add("spring.property", subject, value, FactStatus.DEFAULT, scope=self.scope(), unit=d.unit, inputs=inputs,
                          exposure=expo, exposure_sources=srcs, link_ids=self.link_ids, default_ref=d.ref)
        return Prop(fid, key, value, d.unit, FactStatus.DEFAULT, False, "default", None, expo, default=d)

    # -- actuator

    def derive(self) -> None:
        self._probe_groups()

    def _facts_exposed(self, props: Sequence[Prop | None]) -> tuple[OverrideExposure, list[ExposureSource]]:
        ids = sorted({p.fid for p in props if p is not None and p.exposure is not OverrideExposure.NONE})
        if not ids:
            if self.model is None and any(p is not None for p in props):
                return OverrideExposure.ENV_POSSIBLE, []
            return OverrideExposure.NONE, []
        return OverrideExposure.ENV_POSSIBLE, [ExposureSource(via="inherited", from_fact_ids=ids)]

    def _combine(self, props: list[Prop | None]) -> tuple[FactStatus, str | None]:
        """Status of a value derived from `props`: declared from declared values and cited defaults; inferred when any
        input is inferred; unresolved when any input is missing or unresolved."""
        status = FactStatus.DECLARED
        for p in props:
            if p is None:
                return FactStatus.UNRESOLVED, UNKNOWN_INPUT
            if p.status is FactStatus.UNRESOLVED:
                return FactStatus.UNRESOLVED, p.unresolved_reason or "an input value is unresolved"
            if p.status is FactStatus.INFERRED:
                status = FactStatus.INFERRED
        return status, None

    def _probe_groups(self) -> None:
        subject = f"spring:{self.subject_root}:health"
        enabled_hits = self.hits("management.endpoint.health.probes.enabled")
        platform_hits = self.hits("spring.main.cloud-platform")
        add_paths = self.prop("management.endpoint.health.probes.add-additional-paths")
        enabled = self.prop("management.endpoint.health.probes.enabled", default=False)
        platform = self.prop("spring.main.cloud-platform", default=False)
        expo, srcs = self.exposure("management.endpoint.health.probes.enabled", "default")
        inputs: list[FactInput] = []
        if self.boot_fid is not None:
            inputs.append(via(R.VERSION_BASIS, self.boot_fid))
        groups: dict[str, bool] | None = None
        status = FactStatus.DECLARED
        source: SourceRef | None = None
        default_ref = None
        reason: str | None = None
        precedence: str | None = None
        if enabled is not None:
            if enabled.status is FactStatus.UNRESOLVED:
                reason = enabled.unresolved_reason
            elif isinstance(enabled.value, bool):
                groups = {"liveness": enabled.value, "readiness": enabled.value}
                status = enabled.status
                source = ref(*enabled.source) if enabled.source else None
                inputs.append(via(R.VALUE_SOURCE, enabled.fid))
                precedence = "spring.property"
            else:
                reason = f"management.endpoint.health.probes.enabled is {enabled.value!r}, which is not true or false"
        elif platform is not None and platform.value is not None and platform.status is not FactStatus.UNRESOLVED:
            on = str(platform.value).strip().lower() == "kubernetes"
            groups = {"liveness": on, "readiness": on}
            status = platform.status
            source = ref(*platform.source) if platform.source else None
            inputs.append(via(R.VALUE_SOURCE, platform.fid))
            precedence = "spring.property"
        else:
            d = spring_default("management.endpoint.health.probes.groups", self.version)
            inputs.append(self._absence_input("management.endpoint.health.probes.enabled"))
            if d is None:
                reason = "the Spring Boot version is outside the defaults table"
            elif self.link is None:
                reason = ("the probe groups exist only on a Kubernetes platform (KUBERNETES_SERVICE_HOST is set by the kubelet), and this module is "
                          "not linked to a container in the repository")
            else:
                groups = dict(d.value)
                default_ref = d.ref
                status = FactStatus.DEFAULT
        if groups is None:
            self.probe_groups_fid = self.fb.add("spring.actuator.probe_groups", subject, None, FactStatus.UNRESOLVED, scope=self.scope(),
                                                inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=self.link_ids,
                                                unresolved_reason=reason or "the probe groups cannot be determined")
            self.probe_groups = None
            self._endpoints(None)
            return
        self.probe_groups_fid = self.fb.add("spring.actuator.probe_groups", subject, groups, self._status_for(status), scope=self.scope(),
                                            source=source, inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=self.link_ids,
                                            default_ref=default_ref, precedence=precedence)
        self.probe_groups = groups
        self._endpoints(add_paths)
        _ = enabled_hits, platform_hits

    def _access(self) -> tuple[str | None, FactStatus, str | None, tuple[str, int] | None, list[Prop], Any]:
        """Access of the health endpoint: (value, status, reason, deciding source, input props, default_ref)."""
        model = _is_access_model(self.version)
        if model is None:
            return None, FactStatus.UNRESOLVED, "the Spring Boot version is outside the defaults table", None, [], None
        n_access, n_enabled, n_default, n_enabled_all, n_max = (
            "management.endpoint.health.access", "management.endpoint.health.enabled", "management.endpoints.access.default",
            "management.endpoints.enabled-by-default", "management.endpoints.access.max-permitted")
        got: dict[str, Prop] = {}
        for n in (n_access, n_enabled, n_default, n_enabled_all, n_max):
            p = self.prop(n, default=False)
            if p is not None:
                got[n] = p
        used = list(got.values())
        bad = next((p for p in used if p.status is FactStatus.UNRESOLVED), None)
        if bad is not None:
            return None, FactStatus.UNRESOLVED, bad.unresolved_reason or f"{bad.key} is unresolved", None, used, None
        on, off = ("unrestricted", "none") if model else ("enabled", "disabled")
        if not model:
            for n in (n_access, n_default, n_max):
                if n in got:
                    src = got[n].source
                    self.cov.append(CoverageNotice(code="FN-OPS-002", scope=f"{src[0]}:{src[1]}" if src else "repository",
                                                   message=f"{n} is not known to Spring Boot {self.version}; the setting is ignored."))
        decided: Prop | None = None
        value: str | None = None
        if model and n_access in got:
            decided, value = got[n_access], str(got[n_access].value).strip().lower()
        elif n_enabled in got:
            decided, value = got[n_enabled], on if got[n_enabled].value is True else off
        elif model and n_default in got:
            decided, value = got[n_default], str(got[n_default].value).strip().lower()
        elif n_enabled_all in got:
            decided, value = got[n_enabled_all], on if got[n_enabled_all].value is True else off
        default_ref = None
        if decided is None:
            d = spring_default("endpoint.default_access", self.version)
            if d is None:
                return None, FactStatus.UNRESOLVED, "the Spring Boot version is outside the defaults table", None, used, None
            value, default_ref = str(d.value), d.ref
        if model and value not in ACCESS_ORDER:
            return None, FactStatus.UNRESOLVED, f"access value {value!r} is not none, read-only or unrestricted", None, used, None
        source = decided.source if decided is not None else None
        status = _worst([p.status for p in used]) if decided is not None else FactStatus.DEFAULT
        if model and n_max in got and value is not None:
            cap = str(got[n_max].value).strip().lower()
            if cap in ACCESS_ORDER and ACCESS_ORDER[cap] < ACCESS_ORDER[value]:
                value, source = cap, got[n_max].source
                status, default_ref = _worst([p.status for p in used]), None
        return value, status, None, source, used, default_ref

    def _endpoints(self, add_paths: Prop | None) -> None:
        subject_access = f"spring:{self.subject_root}:health"
        value, a_status, a_reason, a_source, a_props, a_ref = self._access()
        a_inputs: list[FactInput] = [via(R.VALUE_SOURCE, p.fid) for p in a_props]
        if self.boot_fid is not None:
            a_inputs.append(via(R.VERSION_BASIS, self.boot_fid))
        if a_status is FactStatus.DEFAULT or (a_status is not FactStatus.UNRESOLVED and not a_props):
            a_inputs.append(self._absence_input("management.endpoint.health.access"))
        a_expo, a_srcs = self.exposure("management.endpoint.health.access", "default")
        if value is None:
            self.fb.add("spring.actuator.endpoint_access", subject_access, None, FactStatus.UNRESOLVED, scope=self.scope(), inputs=a_inputs,
                        exposure=a_expo, exposure_sources=a_srcs, link_ids=self.link_ids, unresolved_reason=a_reason)
            access_fid = None
        else:
            access_fid = self.fb.add("spring.actuator.endpoint_access", subject_access, value, self._status_for(a_status), scope=self.scope(),
                                     source=ref(*a_source) if a_source else None, inputs=a_inputs, exposure=a_expo, exposure_sources=a_srcs,
                                     link_ids=self.link_ids, default_ref=a_ref, precedence="spring.property" if a_source else None)
        server_port = self.prop("server.port")
        mgmt_port = self.prop("management.server.port")
        base = self.prop("management.endpoints.web.base-path")
        mgmt_base = self.prop("management.server.base-path")
        include = self.prop("management.endpoints.web.exposure.include")
        exclude = self.prop("management.endpoints.web.exposure.exclude")
        mapping = self.prop("management.endpoints.web.path-mapping.health")
        context = self.prop("server.servlet.context-path")
        access_prop = Prop(access_fid, "health.access", value, None, a_status, False, "derived", None, a_expo) if access_fid else None
        probe = self._probe_prop()
        for group in ("liveness", "readiness"):
            self._endpoint(group, probe, add_paths, server_port, mgmt_port, base, mgmt_base, include, exclude, mapping, context, access_prop, value)

    def _probe_prop(self) -> Prop | None:
        fid = getattr(self, "probe_groups_fid", None)
        g = getattr(self, "probe_groups", None)
        if fid is None:
            return None
        f = self.fb.get(fid)
        return Prop(fid, "probes", g, None, f.status, False, "derived", None, f.override_exposure, f.unresolved_reason)

    def _endpoint(self, group: str, probe: Prop | None, add_paths: Prop | None, server_port: Prop | None, mgmt_port: Prop | None,
                  base: Prop | None, mgmt_base: Prop | None, include: Prop | None, exclude: Prop | None, mapping: Prop | None,
                  context: Prop | None, access: Prop | None, access_value: str | None) -> None:
        subject = f"spring:{self.subject_root}:health/{group}"
        mgmt_hit = mgmt_port is not None and mgmt_port.has_hit
        server_hit = server_port is not None and server_port.has_hit
        port_type = "SAME"
        m_val = mgmt_port.value if mgmt_port is not None else None
        s_val = server_port.value if (server_port is not None and server_hit) else None
        if mgmt_hit and isinstance(m_val, int) and m_val < 0:
            port_type = "DISABLED"
        elif mgmt_hit and isinstance(m_val, int):
            if m_val == 0 or not ((s_val is None and m_val == 8080) or (isinstance(s_val, int) and m_val == s_val)):
                port_type = "DIFFERENT"
        base_inputs: list[Prop | None] = [server_port, mgmt_port, base, probe, include, exclude, mapping, access]
        if port_type == "DIFFERENT":
            ins: list[Prop | None] = [*base_inputs, mgmt_base, add_paths]
        else:
            ins = [*base_inputs, context, add_paths]
        status: FactStatus
        reason: str | None
        if any(p is None for p in ins) or probe is None:
            status, reason = FactStatus.UNRESOLVED, UNKNOWN_INPUT
        else:
            status, reason = self._combine(ins)
        present = [p for p in ins if p is not None]
        inputs = [via(R.VALUE_SOURCE, p.fid) for p in present]
        expo, srcs = self._facts_exposed(present)
        if status is FactStatus.UNRESOLVED or access_value is None:
            self.fb.add("spring.actuator.endpoint", subject, None, FactStatus.UNRESOLVED, scope=self.scope(), inputs=inputs, exposure=expo,
                        exposure_sources=srcs, link_ids=self.link_ids, unresolved_reason=reason or "the endpoint access is unknown",
                        precedence="spring.actuator.path_algorithm")
            return
        assert probe is not None and server_port is not None and base is not None and include is not None and exclude is not None and context is not None
        groups = probe.value
        reasons: list[str] = []
        if isinstance(groups, dict) and not groups.get(group, False):
            reasons.append("the probe groups are not enabled")
        inc = {canon(str(v)) for v in (include.value or [])}
        exc = {canon(str(v)) for v in (exclude.value or [])}
        health_exposed = ("health" in inc or "*" in inc) and not ("health" in exc or "*" in exc)
        if not health_exposed:
            reasons.append("the health endpoint is not exposed over HTTP")
        if access_value in ("none", "disabled"):
            reasons.append("the health endpoint access is turned off")
        if port_type == "DISABLED":
            reasons.append("the management server is disabled (management.server.port is negative)")
        source = self._derived_source(port_type, mgmt_port, include, server_port, base)
        if reasons:
            self.fb.add("spring.actuator.endpoint", subject, None, FactStatus.DECLARED, absent=True, scope=self.scope(),
                        source=ref(*source) if source else None, inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=self.link_ids,
                        precedence="spring.actuator.path_algorithm")
            return
        assert mgmt_port is not None
        port: Any = mgmt_port.value if port_type == "DIFFERENT" else (server_port.value if server_hit else 8080)
        if port == 0:
            self.fb.add("spring.actuator.endpoint", subject, None, FactStatus.UNRESOLVED, scope=self.scope(), inputs=inputs, exposure=expo,
                        exposure_sources=srcs, link_ids=self.link_ids, unresolved_reason="the port is 0, so the operating system picks a random port",
                        precedence="spring.actuator.path_algorithm")
            return
        if port_type == "DIFFERENT":
            assert mgmt_base is not None
            root_path = _clean_base(mgmt_base.value)
        else:
            assert context is not None
            root_path = _clean_base(context.value) if context.value else ""
        segment = str(mapping.value).strip("/") if mapping is not None and mapping.value else "health"
        path = f"{root_path}{_clean_base(base.value)}/{segment}/{group}"
        value: dict[str, Any] = {"port": port, "path": path, "port_type": port_type}
        if add_paths is not None and add_paths.value is True:
            extra = "/livez" if group == "liveness" else "/readyz"
            ctx = _clean_base(context.value) if context.value else ""
            value["additional_path"] = {"port": server_port.value if server_hit else 8080, "path": ctx + extra}
        self.fb.add("spring.actuator.endpoint", subject, value, self._status_for(status), scope=self.scope(),
                    source=ref(*source) if source else None, inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=self.link_ids,
                    precedence="spring.actuator.path_algorithm")

    def _derived_source(self, port_type: str, mgmt_port: Prop | None, include: Prop | None, server_port: Prop | None,
                        base: Prop | None) -> tuple[str, int] | None:
        order = [mgmt_port, include, server_port, base] if port_type == "DIFFERENT" else [include, server_port, mgmt_port, base]
        for p in order:
            if p is not None and p.has_hit and p.source is not None:
                return p.source
        return None


# ---------------------------------------------------------------- helpers


def _worst(statuses: list[FactStatus]) -> FactStatus:
    for s in (FactStatus.UNRESOLVED, FactStatus.INFERRED):
        if s in statuses:
            return s
    return FactStatus.DECLARED


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_text(v) for v in value)
    return str(value)


def _short(prec: str) -> str:
    return "os_environment" if prec == "spring.os_environment" else prec.replace("spring.", "")


def _flatten_json(text: str) -> list[tuple[str, Any]] | None:
    if len(text) > 65_536:
        return None
    try:
        data = json.loads(text, parse_constant=lambda _c: (_ for _ in ()).throw(ValueError("constant")))
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    out: list[tuple[str, Any]] = []
    stack: list[tuple[str, Any]] = [("", data)]
    while stack and len(out) < 10_000:
        prefix, node = stack.pop()
        if isinstance(node, dict):
            for k, v in reversed(list(node.items())):
                stack.append((f"{prefix}.{k}" if prefix else str(k), v))
        elif isinstance(node, list):
            for i, v in reversed(list(enumerate(node))):
                stack.append((f"{prefix}[{i}]", v))
        elif prefix:
            out.append((prefix, "" if node is None else node))
    return out


# ---------------------------------------------------------------- entry point


def resolve_spring(ws: Workspace, detection: DetectionResult, dep: DeploymentResult, *, profile: str | None,
                   profile_source: str) -> SpringResult:
    fb = FactBuilder(EXTRACTOR)
    result = SpringResult(facts=fb)
    modules = _find_modules(detection)
    cache: dict[str, list[ConfigDocument] | None] = {}
    option = ([p.strip() for p in profile.split(",") if p.strip()], profile_source) if profile else None
    for root in sorted(modules):
        mod = modules[root]
        mod.kube_config = _kube_config_dependency(ws, mod)
        boot_fid = _build_facts(ws, mod, root, fb, result)
        contexts: list[tuple[str | None, Linkage | None]] = [
            (csub, link) for csub, link in sorted(dep.container_links.items()) if link.to_ref == f"source:{root}"]
        if not contexts:
            if not any(f.kind == KIND_SPRING_CONFIG and f.path.startswith(tuple(d + "/" for d in mod.resources_dirs)) for f in ws.files):
                continue  # no packaged configuration and no container: nothing Spring-specific to resolve for this module
            contexts = [(None, None)]
        for csub, link in contexts:
            subject_root = root if len(contexts) == 1 else f"{root}@{csub or '-'}"
            run = _Run(ws, mod, dep, fb, result.coverage, subject_root, csub, link, option, boot_fid, result.parsed, result.consumed, cache)
            run.resolve_profiles()
            _emit_all(run, mod)
            _coverage(run, mod, result)
    result.parsed = sorted(set(result.parsed))
    return result


def _emit_all(run: _Run, mod: ModuleInfo) -> None:
    keys: dict[str, str] = {}
    registry = {canon(k): k for k in (*SPECS, *DEFAULT_KEYS)}
    for table in (run.files_table, run.sys_table, run.json_table, run.args_table):
        for c, hs in table.items():
            key = registry.get(c, hs[-1].key)
            if is_relevant(key):
                keys.setdefault(c, key)
    ordered = sorted(keys.items())
    for _c, key in ordered[:MAX_PROPERTY_FACTS]:
        run.prop(key)
    if len(ordered) > MAX_PROPERTY_FACTS:
        run.cov.append(CoverageNotice(code="COV-LIMIT", scope=run.subject_root,
                                      message=f"{len(ordered)} relevant Spring properties found; the first {MAX_PROPERTY_FACTS} (by name) were reported."))
    for key in DEFAULT_KEYS:
        run.prop(key)
    run.derive()


def _build_facts(ws: Workspace, mod: ModuleInfo, root: str, fb: FactBuilder, result: SpringResult) -> str | None:
    """stack.version facts for the module's build file (SP-B02 to SP-B05)."""
    suffix = "" if root == "." else f"@{root}"
    boot_fid: str | None = None
    have_framework = False
    for d in sorted(mod.detections, key=lambda x: (x.component, x.source.line_start if x.source else 0)):
        if d.source is None:
            continue
        subject = f"component:{d.component}{suffix}"
        inputs: list[FactInput] = []
        if d.note and d.note.startswith("resolved from property"):
            m = re.search(r"\$\{([^}]+)\}", d.note)
            if m:
                use = _property_use(ws, d.source.path, m.group(0), d.source.line_start)
                if use is not None:
                    inputs.append(FactInput(role=R.PROPERTY_REFERENCE, source=ref(d.source.path, use)))
        if d.version is None:
            fid = fb.add("stack.version", subject, None, FactStatus.UNRESOLVED, source=d.source, inputs=inputs,
                         unresolved_reason=d.note or f"{d.component} version is not stated in the build file")
        else:
            fid = fb.add("stack.version", subject, d.version, FactStatus.DECLARED, source=d.source, inputs=inputs)
        if d is mod.boot_detection:
            boot_fid = fid
        have_framework = have_framework or d.component == "spring-framework"
    if not have_framework and mod.boot_version is not None and boot_fid is not None:
        sd = spring_default("component.spring-framework", mod.boot_version)
        if sd is not None:
            fb.add("stack.version", f"component:spring-framework{suffix}", sd.value, FactStatus.DEFAULT,
                   inputs=[via(R.VERSION_BASIS, boot_fid)], default_ref=sd.ref)
    return boot_fid


KUBE_CONFIG_DEP = re.compile(r"spring-cloud-(?:starter-)?kubernetes[\w-]*config")


def _kube_config_dependency(ws: Workspace, mod: ModuleInfo) -> tuple[str, int] | None:
    paths = sorted({d.source.path for d in mod.detections if d.source is not None})
    for path in paths:
        entry = next((f for f in ws.files if f.path == path), None)
        if entry is None:
            continue
        try:
            for n, text in enumerate(ws.read_text(entry).splitlines(), start=1):
                if KUBE_CONFIG_DEP.search(text):
                    return path, n
        except OSError:
            continue
    return None


def _property_use(ws: Workspace, path: str, token: str, definition_line: int) -> int | None:
    entry = next((f for f in ws.files if f.path == path), None)
    if entry is None:
        return None
    try:
        lines = ws.read_text(entry).splitlines()
    except OSError:
        return None
    for n, text in enumerate(lines, start=1):
        if token in text and n != definition_line:
            return n
    return None


def _coverage(run: _Run, mod: ModuleInfo, result: SpringResult) -> None:
    where = run.subject_root
    if mod.boot_version is None:
        result.coverage.append(CoverageNotice(code="FN-OPS-002", scope=where,
                                              message="The Spring Boot version is not determined, so cited defaults and version-dependent facts "
                                                      "are not produced for this module."))
    elif spring_minor(mod.boot_version) is None:
        result.coverage.append(CoverageNotice(code="COV-UNTESTED-VERSION", scope=where,
                                              message=f"Spring Boot {mod.boot_version} is outside the versions the defaults table covers "
                                                      "(3.2 to 3.5). Defaults and facts derived from them are not produced."))
    for doc in run.included_docs:
        for value, line in doc.imports:
            if not value.strip():
                continue
            result.coverage.append(CoverageNotice(
                code="FN-OPS-002", scope=f"{doc.path}:{line}",
                message=f"spring.config.import {value!r} is not followed. Values it supplies are unknown and could replace values read here."))
    if run.model is not None and run.model.unknown_sources():
        names = ", ".join(sorted(f"{s.kind}/{s.name}" for s in run.model.unknown_sources()))
        result.coverage.append(CoverageNotice(
            code="FN-OPS-002", scope=run.csub or where,
            message=f"{names} is attached to the container but is not in the repository. It can set environment variables that replace "
                    "values read from the configuration files, SPRING_PROFILES_ACTIVE excepted when the Deployment sets it explicitly."))
    for var in ("SPRING_CONFIG_LOCATION", "SPRING_CONFIG_ADDITIONAL_LOCATION", "SPRING_CONFIG_IMPORT", "SPRING_CONFIG_NAME"):
        if run.env_get(var):
            hit = run.env_get(var)[0]
            result.coverage.append(CoverageNotice(
                code="FN-OPS-002", scope=run._where(hit),
                message=f"{var} changes where Spring Boot reads configuration. Files outside the packaged resources are not read, so values may differ."))
