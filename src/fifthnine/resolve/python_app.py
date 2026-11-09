"""M2-3: Python application facts, read from source with `ast` and from the container command. Nothing is imported or run.

Per source root (a directory with a Python build file): component versions, pydantic-settings fields, tenacity `@retry`
stop and wait, the httpx clients those retried functions call, `asyncio.timeout` style deadlines in route handlers, and which
retried functions a route handler calls. Per container linked to the root: the ASGI application the effective command loads,
`FastAPI(debug=...)`, the application's routes, and gunicorn or uvicorn settings from the command line.

Every value is a literal in the repository, a cited default for a detected version, or unresolved with a reason. Dynamic
constructs (factories, loops, conditional registration, values computed at import time) are not followed; where they could
change a reported value the fact is unresolved or the route table is marked incomplete.
"""

from __future__ import annotations

import ast
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from fifthnine.core.detect import Detection, DetectionResult
from fifthnine.core.facts import FactBuilder, loc, ref, via
from fifthnine.core.safeparse import load_yaml_documents, to_plain
from fifthnine.core.workspace import KIND_PYTHON_SOURCE, Workspace
from fifthnine.resolve import shellcmd
from fifthnine.resolve.deployment import DeploymentResult, EnvModel
from fifthnine.schema.v1 import (
    CoverageNotice,
    DefaultRef,
    ExposureSource,
    FactInput,
    FactStatus,
    InputRole,
    Linkage,
    OverrideExposure,
    Reference,
    UpstreamRef,
)

EXTRACTOR = "python-m2.3"
R = InputRole
PY_COMPONENTS = ("fastapi", "uvicorn", "gunicorn", "httpx", "tenacity", "python")
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
CLIENT_CALLS = (*HTTP_METHODS, "request", "stream", "send")
MAX_MODULES = 5000


# ---------------------------------------------------------------- defaults table


@dataclass(frozen=True)
class PyDefault:
    value: Any
    unit: str | None
    ref: DefaultRef


@cache
def _table() -> dict[str, Any]:
    text = resources.files("fifthnine.data").joinpath("defaults/python.v1.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = to_plain(load_yaml_documents(text)[0].data)
    return data


def py_default(key: str, version: str | None) -> PyDefault | None:
    """The cited default for `key` when `version` is one of the releases where it was verified; otherwise None."""
    table = _table()
    e = table["entries"].get(key)
    if e is None or version is None or str(version) not in [str(v) for v in e.get("verified_at") or []]:
        return None
    fw = table["frameworks"][e["framework"]]
    tag = str(fw["cited_tag"])
    upstream = UpstreamRef(repository=fw["repository"], tag=tag, commit=fw["commit"], path=e["path"], line=e["line"],
                           anchor=e["anchor"], verified_at=[str(v) for v in e["verified_at"]])
    url = f"https://github.com/{fw['repository']}/blob/{fw['commit']}/{e['path']}#L{e['line']}"
    vs = [str(v) for v in e["verified_at"]]
    dref = DefaultRef(framework=e["framework"], version_range=f"{vs[0]} to {vs[-1]} (each release verified)",
                      reference=Reference(title=f"{e['framework']} {tag} {e['path']}:{e['line']}", url=url, source_path=f"{tag}:{e['path']}"),
                      applied_version=str(version), upstream=upstream)
    return PyDefault(e["value"], e.get("unit"), dref)


# ---------------------------------------------------------------- module index


@dataclass
class PyModule:
    name: str
    path: str
    tree: ast.Module
    lines: list[str]
    imports: dict[str, str] = field(default_factory=dict)  # local name -> dotted target


class Index:
    """The Python modules of one source root, keyed by dotted name relative to the root. Modules are parsed on demand, and the
    scans over every module first test the raw text for a keyword, so a large repository is not held in memory as trees."""

    def __init__(self, ws: Workspace, root: str, cov: list[CoverageNotice]) -> None:
        self.ws, self.root, self.cov = ws, root, cov
        self.entries: dict[str, tuple[Any, bool]] = {}
        self._cache: dict[str, PyModule | None] = {}
        prefix = "" if root == "." else root.rstrip("/") + "/"
        files = sorted((f for f in ws.files if f.kind == KIND_PYTHON_SOURCE and f.path.startswith(prefix)), key=lambda f: f.path)
        for entry in files[:MAX_MODULES]:
            rel = entry.path[len(prefix):]
            parts = rel[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts or any(not p.isidentifier() for p in parts):
                continue
            self.entries[".".join(parts)] = (entry, rel.endswith("__init__.py"))
        if len(files) > MAX_MODULES:
            cov.append(CoverageNotice(code="COV-LIMIT", scope=root, message=f"Only the first {MAX_MODULES} Python modules were read."))

    @property
    def parsed(self) -> list[str]:
        return sorted(m.path for m in self._cache.values() if m is not None)

    def get(self, name: str, text: str | None = None) -> PyModule | None:
        if name in self._cache:
            return self._cache[name]
        if name not in self.entries:
            return None
        entry, is_pkg = self.entries[name]
        try:
            text = text if text is not None else self.ws.read_text(entry)
            tree = ast.parse(text)
        except (SyntaxError, ValueError, OSError, RecursionError, MemoryError):
            self.cov.append(CoverageNotice(code="FN-OPS-002", scope=entry.path, message="Python source could not be parsed; facts from it are missing."))
            self._cache[name] = None
            return None
        mod = PyModule(name, entry.path, tree, text.splitlines())
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    mod.imports[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                base = _absolute(name, is_pkg, node.module, node.level)
                for a in node.names:
                    if a.name != "*":
                        mod.imports[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
        self._cache[name] = mod
        return mod

    def candidates(self, *keywords: str) -> list[PyModule]:
        """Modules whose text contains one of the keywords, parsed."""
        out: list[PyModule] = []
        for name, (entry, _pkg) in self.entries.items():
            if name in self._cache:
                m = self._cache[name]
                if m is not None and any(k in "\n".join(m.lines) for k in keywords):
                    out.append(m)
                continue
            try:
                text = self.ws.read_text(entry)
            except OSError:
                continue
            if any(k in text for k in keywords):
                m = self.get(name, text)
                if m is not None:
                    out.append(m)
        return out

    def dotted(self, mod: PyModule, expr: ast.expr) -> str | None:
        """Dotted name an expression refers to, through the module's imports (`inventory.fetch_stock` -> app.clients.inventory.fetch_stock)."""
        if isinstance(expr, ast.Name):
            if expr.id in mod.imports:
                return mod.imports[expr.id]
            return f"{mod.name}.{expr.id}"
        if isinstance(expr, ast.Attribute):
            base = self.dotted(mod, expr.value)
            return f"{base}.{expr.attr}" if base else None
        return None

    def split(self, dotted: str) -> tuple[PyModule, str] | None:
        """(module, attribute) for a dotted name whose prefix is a module of this root."""
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            if i == len(parts) - 1 and ".".join(parts[:i]) in self.entries:
                m = self.get(".".join(parts[:i]))
                if m is not None:
                    return m, parts[-1]
        return None

    def top_assign(self, mod: PyModule, name: str) -> ast.expr | None:
        found: ast.expr | None = None
        for node in mod.tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                found = node.value
        return found

    def top_def(self, mod: PyModule, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for node in mod.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    def resolve_object(self, dotted: str, depth: int = 0) -> tuple[PyModule, str, ast.expr] | None:
        """Follow a dotted name to the module-level assignment that defines it, through re-exports."""
        if depth > 8:
            return None
        sp = self.split(dotted)
        if sp is None:
            return None
        mod, attr = sp
        value = self.top_assign(mod, attr)
        if value is not None:
            return mod, attr, value
        if attr in mod.imports and mod.imports[attr] != dotted:
            return self.resolve_object(mod.imports[attr], depth + 1)
        return None


def _absolute(name: str, is_pkg: bool, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""
    parts = name.split(".") if is_pkg else name.split(".")[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    return ".".join([*parts, *([module] if module else [])])


def _literal(node: ast.expr | None) -> tuple[bool, Any]:
    if node is None:
        return False, None
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def _is(index: Index, mod: PyModule, expr: ast.expr, *targets: str) -> bool:
    d = index.dotted(mod, expr)
    return d is not None and d in targets


def _kw(call: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in call.keywords if k.arg == name), None)


def _anchor(mod: PyModule, line: int) -> str:
    return mod.lines[line - 1].strip() if 0 < line <= len(mod.lines) else ""


# ---------------------------------------------------------------- the resolver


@dataclass
class PythonResult:
    facts: FactBuilder
    coverage: list[CoverageNotice] = field(default_factory=list)
    parsed: list[str] = field(default_factory=list)


@dataclass
class _EnvVal:
    value: str | None
    known: bool
    source: tuple[str, int] | None
    via: str | None
    exposure: list[ExposureSource]
    layer: str  # env | docker_env | external | unknown


@dataclass
class _Cli:
    values: dict[str, list[tuple[str | None, int]]] = field(default_factory=dict)  # setting -> [(value, word index)]
    app: tuple[str, int] | None = None
    opaque: str | None = None


_BAD = object()


@cache
def _cli_table() -> dict[str, dict[str, dict[str, Any]]]:
    text = resources.files("fifthnine.data").joinpath("python_cli.v1.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = to_plain(load_yaml_documents(text)[0].data)
    return {"gunicorn": data["gunicorn"], "uvicorn": data["uvicorn"]}


def _parse_cli(server: str, words: list[str], *, positional: bool = True) -> _Cli:
    """Parse the arguments after the server name as argparse (gunicorn: long options by unique prefix, `-w4`, `-w=4`) or click
    (uvicorn: no abbreviations) would, from the option tables. An option outside the table makes the command opaque."""
    table = _cli_table()[server]
    out = _Cli()
    i = 0
    rest_positional = False
    while i < len(words):
        w = words[i]
        if w == "--" and not rest_positional:
            rest_positional = True
            i += 1
            continue
        if not rest_positional and w.startswith("--"):
            flag, eq, val = w.partition("=")
            opt = flag if flag in table else None
            if opt is None and server == "gunicorn":
                matches = [k for k in table if k.startswith(flag) and k.startswith("--")]
                opt = matches[0] if len(matches) == 1 else None
            if opt is None:
                out.opaque = f"option {flag} is not in the option table"
                return out
            takes = table[opt]["value"]
            setting = str(table[opt]["setting"])
            if takes == "optional":
                out.opaque = f"option {opt} takes an optional value"
                return out
            if takes:
                if not eq:
                    if i + 1 >= len(words):
                        out.opaque = f"option {opt} has no value"
                        return out
                    val = words[i + 1]
                    i += 1
                out.values.setdefault(setting, []).append((val, i))
            else:
                if eq:
                    out.opaque = f"flag {opt} is given a value"
                    return out
                out.values.setdefault(setting, []).append((None, i))
            i += 1
            continue
        if not rest_positional and w.startswith("-") and len(w) > 1:
            opt = w[:2]
            if opt not in table:
                out.opaque = f"option {opt} is not in the option table"
                return out
            takes = table[opt]["value"]
            setting = str(table[opt]["setting"])
            attached = w[2:]
            if takes is True:
                if attached.startswith("="):
                    attached = attached[1:]
                if attached:
                    out.values.setdefault(setting, []).append((attached, i))
                elif i + 1 < len(words):
                    out.values.setdefault(setting, []).append((words[i + 1], i))
                    i += 1
                else:
                    out.opaque = f"option {opt} has no value"
                    return out
            elif attached:
                out.opaque = f"combined short options {w} are not modelled"
                return out
            else:
                out.values.setdefault(setting, []).append((None, i))
            i += 1
            continue
        if positional and out.app is None:
            out.app = (w, i)
        elif positional:
            out.opaque = f"unexpected argument {w}"
            return out
        i += 1
    return out


def _int_or(raw: str | None, numeric: bool) -> Any:
    if numeric and raw is not None and re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _click_value(raw: str, name: str) -> Any:
    if name in ("port", "timeout_graceful_shutdown"):
        return int(raw) if re.fullmatch(r"-?\d+", raw.strip()) else _BAD
    if name == "reload":
        v = raw.strip().lower()
        if v in ("1", "true", "t", "yes", "y", "on"):
            return True
        if v in ("0", "false", "f", "no", "n", "off", ""):
            return False
        return _BAD
    return raw


@dataclass
class _Setting:
    fid: str
    value: Any
    known: bool
    exposure: OverrideExposure
    link_ids: list[str]


def resolve_python(ws: Workspace, detection: DetectionResult, dep: DeploymentResult) -> PythonResult:
    fb = FactBuilder(EXTRACTOR)
    res = PythonResult(fb)
    roots = _roots(detection)
    for root in sorted(roots):
        dets = roots[root]
        versions = _stack_facts(fb, root, dets)
        contexts = [(csub, link) for csub, link in sorted(dep.container_links.items()) if link.to_ref == f"source:{root}"]
        index = Index(ws, root, res.coverage)
        if not index.entries:
            continue
        run = _Root(ws, index, fb, res, dep, versions, contexts)
        for csub, link in contexts:
            run.container(csub, link)
        run.settings()
        run.retries()
        run.handlers()
        res.parsed += index.parsed
    res.parsed = sorted(set(res.parsed))
    return res


def _roots(detection: DetectionResult) -> dict[str, list[Detection]]:
    out: dict[str, list[Detection]] = {}
    for d in detection.detections + detection.unresolved:
        if d.component not in PY_COMPONENTS or d.source is None:
            continue
        root = posixpath.dirname(d.source.path) or "."
        out.setdefault(root, []).append(d)
    # a root needs a Python build file; a Dockerfile alone (python base image) attaches to the build root at the same place
    return {r: ds for r, ds in out.items() if any(d.component != "python" for d in ds)}


def _stack_facts(fb: FactBuilder, root: str, dets: list[Detection]) -> dict[str, tuple[str | None, str]]:
    suffix = "" if root == "." else f"@{root}"
    out: dict[str, tuple[str | None, str]] = {}
    for d in sorted(dets, key=lambda x: (x.component, x.source.path if x.source else "", x.source.line_start if x.source else 0)):
        if d.source is None or d.component in out:
            continue
        subject = f"component:{d.component}{suffix}"
        if d.version is None:
            fid = fb.add("stack.version", subject, None, FactStatus.UNRESOLVED, source=d.source,
                         unresolved_reason=d.note or f"{d.component} version is not stated exactly in the build file")
        else:
            fid = fb.add("stack.version", subject, d.version, FactStatus.DECLARED, source=d.source)
        out[d.component] = (d.version, fid)
    return out


class _Root:
    def __init__(self, ws: Workspace, index: Index, fb: FactBuilder, res: PythonResult, dep: DeploymentResult,
                 versions: dict[str, tuple[str | None, str]], contexts: list[tuple[str, Linkage]]) -> None:
        self.ws, self.index, self.fb, self.res, self.dep = ws, index, fb, res, dep
        self.versions = versions
        self.contexts = contexts
        self.settings_fields: dict[str, _Setting] = {}  # "module.Class.field" -> setting
        self.instances: dict[str, str] = {}  # "module.name" of a module-level Settings() instance -> "module.Class"
        self.retry_funcs: dict[str, str] = {}  # dotted function -> its python subject
        self.clients: dict[str, str] = {}  # dotted client variable -> timeout fact id
        self.routes: dict[str, str] = {}  # dotted endpoint function -> route fact id (first route that names it)
        self._docker_cache: dict[str, dict[str, tuple[str, int, str, bool]]] = {}
        self.env_changed: set[str] = set()
        self.settings_classes: dict[str, tuple[PyModule, ast.ClassDef]] = {}  # containers whose server sets extra environment variables (--env, --env-file)

    def _cov(self, scope: str, message: str) -> None:
        self.res.coverage.append(CoverageNotice(code="FN-OPS-002", scope=scope, message=message))

    def _default(self, kind: str, subject: str, key: str, component: str, absence: tuple[str, int], *, link_ids: list[str] | None = None,
                 extra_inputs: list[FactInput] | None = None, exposure: OverrideExposure = OverrideExposure.NONE,
                 exposure_sources: list[ExposureSource] | None = None, precedence: str | None = None,
                 value_map: Any = None) -> str:
        version, vfid = self.versions.get(component, (None, None))
        inputs = [loc(R.ABSENCE, *absence), *([via(R.VERSION_BASIS, vfid)] if vfid else []), *(extra_inputs or [])]
        d = py_default(key, version)
        if d is None:
            why = (f"{component} version is not resolved" if version is None else
                   f"no cited default for {component} {version} (the table covers the releases it verified)")
            return self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, inputs=inputs, link_ids=link_ids, exposure=exposure,
                               exposure_sources=exposure_sources, precedence=precedence, unresolved_reason=f"the setting is not given and {why}")
        value = d.value if value_map is None else value_map(d.value)
        return self.fb.add(kind, subject, value, FactStatus.DEFAULT, unit=d.unit, inputs=inputs, default_ref=d.ref, link_ids=link_ids,
                           exposure=exposure, exposure_sources=exposure_sources, precedence=precedence)

    # -- pydantic-settings

    def _env_models(self) -> list[tuple[str, EnvModel | None, Linkage]]:
        return [(csub, self.dep.env_models.get(csub), link) for csub, link in self.contexts]

    def settings(self) -> None:
        classes: dict[str, tuple[PyModule, ast.ClassDef]] = {}

        def is_settings(dotted: str, depth: int = 0) -> bool:
            if dotted in ("pydantic_settings.BaseSettings", "pydantic_settings.main.BaseSettings"):
                return True
            if depth > 10 or dotted not in classes:
                return False
            m, c = classes[dotted]
            return any(is_settings(self.index.dotted(m, b) or "", depth + 1) for b in c.bases)

        keywords = ["BaseSettings"]
        used: set[str] = set()
        for _pass in range(4):  # subclasses of a settings class can live in modules that never name BaseSettings
            for mod in self.index.candidates(*keywords):
                for node in mod.tree.body:
                    if isinstance(node, ast.ClassDef):
                        classes.setdefault(f"{mod.name}.{node.name}", (mod, node))
            used.update(keywords)
            keywords = sorted({d.rsplit(".", 1)[1] for d in classes if is_settings(d)} - used)
            if not keywords:
                break
        self.settings_classes = {d: v for d, v in classes.items() if is_settings(d)}
        for dotted, (mod, cls) in sorted(classes.items()):
            if not is_settings(dotted) or dotted in ("pydantic_settings.BaseSettings",):
                continue
            prefix, env_file = self._settings_config(mod, cls)
            for node in cls.body:
                if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
                    continue
                name = node.target.id
                if name.startswith("_") or name == "model_config" or "ClassVar" in ast.unparse(node.annotation):
                    continue
                self._settings_field(mod, cls, name, node, prefix, env_file)
        names = sorted({d.rsplit(".", 1)[1] for d in classes if is_settings(d)})
        for mod in (self.index.candidates(*names) if names else []):  # module-level instances: settings = Settings()
            for node in mod.tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                        and isinstance(node.value, ast.Call) and not node.value.args and not node.value.keywords:
                    cls_dotted = self.index.dotted(mod, node.value.func)
                    if cls_dotted is not None and cls_dotted in classes and is_settings(cls_dotted):
                        self.instances[f"{mod.name}.{node.targets[0].id}"] = cls_dotted

    def _settings_config(self, mod: PyModule, cls: ast.ClassDef, depth: int = 0) -> tuple[str | None, bool]:
        """(env_prefix, special) for a settings class, with model_config inherited from settings base classes in the repository.
        None prefix: not a literal. special: an env file, secrets directory, case-sensitive matching or customised sources."""
        prefix: str | None = ""
        special = False
        if depth < 10:
            for b in reversed(cls.bases):  # later bases first, so the first base wins, as in the class's MRO for these keys
                d = self.index.dotted(mod, b) or ""
                if d in self.settings_classes:
                    bmod, bcls = self.settings_classes[d]
                    bp, bs = self._settings_config(bmod, bcls, depth + 1)
                    prefix, special = (bp if bp != "" else prefix), special or bs
        for node in cls.body:
            call: ast.Call | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "settings_customise_sources":
                special = True
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                tgt = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(tgt, ast.Name) and tgt.id == "model_config" and isinstance(node.value, ast.Call):
                    call = node.value
                elif isinstance(tgt, ast.Name) and tgt.id == "model_config" and node.value is not None:
                    return None, True
            if call is not None:
                for k in call.keywords:
                    if k.arg == "env_prefix":
                        ok, v = _literal(k.value)
                        prefix = v if ok and isinstance(v, str) else None
                    elif k.arg in ("env_file", "secrets_dir", "case_sensitive", "env_nested_delimiter") or k.arg is None:
                        special = True
            if isinstance(node, ast.ClassDef) and node.name == "Config":
                for sub in node.body:
                    if isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Name):
                        if sub.targets[0].id == "env_prefix":
                            ok, v = _literal(sub.value)
                            prefix = v if ok and isinstance(v, str) else None
                        elif sub.targets[0].id in ("env_file", "secrets_dir", "case_sensitive"):
                            special = True
        return prefix, special

    def _settings_field(self, mod: PyModule, cls: ast.ClassDef, name: str, node: ast.AnnAssign, prefix: str | None, special: bool) -> None:
        subject = f"python:{mod.name}.{cls.name}#{name}"
        value_node: ast.expr | None = node.value
        env: str | None = (prefix + name).upper() if prefix is not None else None
        required = value_node is None
        default_ok, default = (True, None) if required else _literal(value_node)
        ann_field = None
        if isinstance(node.annotation, ast.Subscript) and _is(self.index, mod, node.annotation.value, "typing.Annotated", "typing_extensions.Annotated"):
            elts = node.annotation.slice.elts if isinstance(node.annotation.slice, ast.Tuple) else []
            ann_field = next((e for e in elts if isinstance(e, ast.Call) and _is(self.index, mod, e.func, "pydantic.Field", "pydantic.fields.Field")), None)
        if ann_field is not None:
            for alias_kw in ("validation_alias", "alias"):
                k = _kw(ann_field, alias_kw)
                if k is not None:
                    ok, v = _literal(k.value)
                    env = v.upper() if ok and isinstance(v, str) else None
                    break
            if _kw(ann_field, "default_factory") is not None or ann_field.args or _kw(ann_field, "default") is not None:
                default_ok = False
        if isinstance(value_node, ast.Call) and _is(self.index, mod, value_node.func, "pydantic.Field", "pydantic.fields.Field"):
            call = value_node
            d_node = call.args[0] if call.args else (_kw(call, "default").value if _kw(call, "default") else None)  # type: ignore[union-attr]
            required = d_node is None or (isinstance(d_node, ast.Constant) and d_node.value is Ellipsis)
            default_ok, default = (True, None) if required else _literal(d_node)
            if _kw(call, "default_factory") is not None:
                default_ok, required = False, False
            for alias_kw in ("validation_alias", "alias"):
                k = _kw(call, alias_kw)
                if k is not None:
                    ok, v = _literal(k.value)
                    env = v.upper() if ok and isinstance(v, str) else None
                    break
        exposure, sources, link_ids, env_value = self._settings_exposure(env)
        known = default_ok and not special and env is not None
        value = {"default": default, "env": env} if not required else {"default": None, "env": env, "required": True}
        src = ref(mod.path, node.lineno)
        if not default_ok or env is None:
            reason = ("the default is not a literal" if not default_ok else "the environment variable name is not a literal")
            fid = self.fb.add("python.settings_field", subject, None, FactStatus.UNRESOLVED, source=src, exposure=exposure,
                              exposure_sources=sources, link_ids=link_ids, unresolved_reason=reason)
        else:
            fid = self.fb.add("python.settings_field", subject, value, FactStatus.DECLARED, source=src, exposure=exposure,
                              exposure_sources=sources, link_ids=link_ids)
        if special:
            self._cov(f"{mod.path}:{cls.lineno}", f"{cls.name} reads an env file or secrets directory, matches names case-sensitively, "
                      "or customises its sources; "
                      "values that depend on its fields are unresolved.")
        effective = env_value if env_value is not None else (None if required else default)
        self.settings_fields[f"{mod.name}.{cls.name}.{name}"] = _Setting(
            fid, _coerce(effective, ast.unparse(node.annotation)), known and not required and env_value is None or (env_value is not None),
            exposure, link_ids)

    def _settings_exposure(self, env: str | None) -> tuple[OverrideExposure, list[ExposureSource], list[str], str | None]:
        """Exposure of an env-bound field across the containers that run this root, and the value the container environment gives it
        (Kubernetes env over the image ENV; pydantic-settings matches names case-insensitively by default)."""
        models = self._env_models()
        link_ids = [link.link_id for _c, _m, link in models]
        if not models:
            return OverrideExposure.ENV_POSSIBLE, [], [], None  # no container is linked: what the environment sets is unknown
        sources: list[ExposureSource] = []
        env_value: str | None = None
        for csub, model, _link in models:
            if model is None or env is None:
                return OverrideExposure.ENV_POSSIBLE, sources, link_ids, None
            if csub in self.env_changed:
                sources.append(ExposureSource(via="valueFrom", object=f"{csub}#server-env", variables=[env]))
            v = self.env_lookup(csub, env, case_insensitive=True)
            if v is None:
                continue
            for x in v.exposure:
                x2 = x.model_copy(update={"variables": [env]})
                if all((y.via, y.object) != (x2.via, x2.object) for y in sources):
                    sources.append(x2)
            if v.layer in ("env", "docker_env"):
                if v.known and len(models) == 1:
                    env_value = v.value
                elif not v.known:
                    sources.append(ExposureSource(via="valueFrom", object=f"{csub}#{env}", variables=[env]))
        return (OverrideExposure.ENV_POSSIBLE if sources else OverrideExposure.NONE), sources, link_ids, env_value

    # -- tenacity and httpx

    def retries(self) -> None:
        for mod in self.index.candidates("tenacity"):  # the decorator is imported from tenacity in the module that uses it
            for node in mod.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        target = dec.func if isinstance(dec, ast.Call) else dec
                        if _is(self.index, mod, target, "tenacity.retry"):
                            self._retry(mod, node, dec)
                            break

    def _retry(self, mod: PyModule, fn: ast.FunctionDef | ast.AsyncFunctionDef, dec: ast.expr) -> None:
        subject = f"python:{mod.name}.{fn.name}"
        self.retry_funcs[f"{mod.name}.{fn.name}"] = subject
        call = dec if isinstance(dec, ast.Call) else None
        for kind, key, parse in (("python.retry.stop", "stop", self._stop), ("python.retry.wait", "wait", self._wait)):
            kw = _kw(call, key) if call is not None else None
            if kw is None:
                if call is not None and any(k.arg is None for k in call.keywords):
                    self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, dec.lineno),
                                unresolved_reason="the decorator takes **kwargs, which may set it")
                    continue
                self._default(kind, subject, f"tenacity.{key}", "tenacity", (mod.path, dec.lineno))
                continue
            value = parse(mod, kw.value)
            src = ref(mod.path, kw.value.lineno)
            if value is None:
                self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=src,
                            unresolved_reason=f"{ast.unparse(kw.value)[:80]} is not a strategy with literal arguments the scanner reads")
            else:
                self.fb.add(kind, subject, value, FactStatus.DECLARED, source=src)
        self._retry_clients(mod, fn, subject)

    def _strategy(self, mod: PyModule, e: ast.expr, family: str) -> tuple[str, dict[str, Any]] | None:
        call = e if isinstance(e, ast.Call) else None
        target = call.func if call is not None else e
        d = self.index.dotted(mod, target)
        if d is None or not d.startswith("tenacity."):
            return None
        name = d.rsplit(".", 1)[-1]
        if not name.startswith(family):
            return None
        args: dict[str, Any] = {}
        if call is not None:
            if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
                return None
            for i, a in enumerate(call.args):
                ok, v = _literal(a)
                if not ok:
                    return None
                args[f"_{i}"] = v
            for k in call.keywords:
                ok, v = _literal(k.value)
                if not ok:
                    return None
                args[str(k.arg)] = v
        elif name not in ("stop_never",):
            return None  # an uncalled strategy class
        return name, args

    def _stop(self, mod: PyModule, e: ast.expr) -> dict[str, Any] | None:
        if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.BitOr, ast.BitAnd)):
            left, right = self._stop(mod, e.left), self._stop(mod, e.right)
            if left is None or right is None:
                return None
            return {"strategy": "any" if isinstance(e.op, ast.BitOr) else "all", "of": [left, right]}
        s = self._strategy(mod, e, "stop_")
        if s is None:
            return None
        name, args = s
        if name == "stop_never":
            return {"strategy": "stop_never"}
        if name == "stop_after_attempt" and set(args) <= {"_0", "max_attempt_number"} and len(args) == 1:
            return {"strategy": name, "attempts": next(iter(args.values()))}
        if name in ("stop_after_delay", "stop_before_delay") and set(args) <= {"_0", "max_delay"} and len(args) == 1:
            return {"strategy": name, "seconds": next(iter(args.values()))}
        return None

    WAIT_PARAMS = {
        "wait_fixed": ["wait"], "wait_none": [], "wait_random": ["min", "max"],
        "wait_exponential": ["multiplier", "max", "exp_base", "min"], "wait_random_exponential": ["multiplier", "max", "exp_base", "min"],
        "wait_exponential_jitter": ["initial", "max", "exp_base", "jitter"], "wait_incrementing": ["start", "increment", "max"],
    }

    def _wait(self, mod: PyModule, e: ast.expr) -> dict[str, Any] | None:
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            left, right = self._wait(mod, e.left), self._wait(mod, e.right)
            return None if left is None or right is None else {"strategy": "sum", "of": [left, right]}
        s = self._strategy(mod, e, "wait_")
        if s is None or s[0] not in self.WAIT_PARAMS:
            return None
        name, args = s
        params = self.WAIT_PARAMS[name]
        out: dict[str, Any] = {"strategy": name}
        for k, v in args.items():
            if k.startswith("_"):
                i = int(k[1:])
                if i >= len(params):
                    return None
                k = params[i]
            elif k not in params:
                return None
            out["seconds" if (name, k) == ("wait_fixed", "wait") else k] = v
        return out

    def _retry_clients(self, mod: PyModule, fn: ast.FunctionDef | ast.AsyncFunctionDef, subject: str) -> None:
        seen: dict[str, int] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CLIENT_CALLS:
                d = self.index.dotted(mod, node.func.value)
                if d is None or d in seen:
                    continue
                obj = self.index.resolve_object(d)
                if obj is None or not isinstance(obj[2], ast.Call):
                    continue
                cmod, cname, cval = obj
                if not isinstance(cval, ast.Call) or not _is(self.index, cmod, cval.func, "httpx.Client", "httpx.AsyncClient"):
                    continue
                seen[d] = node.lineno
                tfid = self._client_timeout(cmod, cname, cval)
                cdotted = f"{cmod.name}.{cname}"
                scope = {} if len(seen) == 1 else {"client": cdotted}
                rk = _kw(node, "timeout")
                if rk is None and any(k.arg is None for k in node.keywords):
                    self.fb.add("python.retry.client", subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, node.lineno), scope=scope,
                                inputs=[via(R.REFERENCE, tfid)], unresolved_reason="the request passes **kwargs, which may set a timeout")
                elif rk is None:
                    self.fb.add("python.retry.client", subject, {"client": f"python:{cdotted}"}, FactStatus.DECLARED,
                                source=ref(mod.path, node.lineno), scope=scope, inputs=[via(R.REFERENCE, tfid)])
                else:
                    rt = self._timeout_value(mod, rk.value)
                    if rt is None:
                        self.fb.add("python.retry.client", subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, node.lineno), scope=scope,
                                    inputs=[via(R.REFERENCE, tfid)], unresolved_reason="the request sets a timeout that is not a literal")
                    else:  # a per-request timeout replaces the client's for this request
                        self.fb.add("python.retry.client", subject, {"client": f"python:{cdotted}", "request_timeout": rt}, FactStatus.DECLARED,
                                    source=ref(mod.path, node.lineno), scope=scope, inputs=[via(R.REFERENCE, tfid)])

    def _client_timeout(self, mod: PyModule, name: str, call: ast.Call) -> str:
        dotted = f"{mod.name}.{name}"
        if dotted in self.clients:
            return self.clients[dotted]
        subject = f"python:{dotted}"
        kind = "python.http_client.timeout"
        kw = _kw(call, "timeout")
        later = [n for n in ast.walk(mod.tree) if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == name and t.attr == "timeout" for t in n.targets)]
        if later:  # client.timeout = ... replaces the constructor value
            top = [n for n in later if n in mod.tree.body]
            if len(later) == len(top) == 1:
                kw = ast.keyword(arg="timeout", value=top[0].value)
            else:
                fid = self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, later[-1].lineno),
                                  unresolved_reason="the client's timeout is reassigned in code the scanner does not follow")
                self.clients[dotted] = fid
                return fid
        if kw is None:
            if any(k.arg is None for k in call.keywords) or call.args:
                fid = self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, call.lineno),
                                  unresolved_reason="the client is built with arguments that may set the timeout")
            else:
                fid = self._default(kind, subject, "httpx.timeout", "httpx", (mod.path, call.lineno))
        else:
            value = self._timeout_value(mod, kw.value)
            src = ref(mod.path, kw.value.lineno)
            if value is None:
                fid = self.fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=src,
                                  unresolved_reason=f"timeout={ast.unparse(kw.value)[:60]} is not a literal or an httpx.Timeout with literal arguments")
            else:
                fid = self.fb.add(kind, subject, value, FactStatus.DECLARED, unit="s", source=src)
        self.clients[dotted] = fid
        return fid

    def _timeout_value(self, mod: PyModule, e: ast.expr) -> dict[str, Any] | None:
        ok, v = _literal(e)
        if ok and (v is None or isinstance(v, (int, float))) and not isinstance(v, bool):
            f = None if v is None else float(v)
            return {"connect": f, "read": f, "write": f, "pool": f}
        if ok and isinstance(v, tuple) and len(v) == 4:
            return dict(zip(("connect", "read", "write", "pool"), (None if x is None else float(x) for x in v), strict=True))
        if isinstance(e, ast.Call) and _is(self.index, mod, e.func, "httpx.Timeout"):
            phases = ("connect", "read", "write", "pool")
            base: Any = "unset"
            if e.args:
                if len(e.args) > 1:
                    return None
                ok, base = _literal(e.args[0])
                if not ok:
                    return None
            vals: dict[str, Any] = {}
            for k in e.keywords:
                ok, kv = _literal(k.value)
                if not ok:
                    return None
                if k.arg == "timeout":
                    base = kv
                elif k.arg in phases:
                    vals[k.arg] = kv
                else:
                    return None
            if base == "unset" and set(vals) != set(phases):
                return None  # httpx raises ValueError: a default or all four values are required
            out = {p: vals.get(p, base) for p in phases}
            if isinstance(base, tuple):
                return None
            return {p: (None if out[p] is None else float(out[p])) for p in phases}
        return None

    # -- containers: ASGI app, routes, server settings

    # -- the container environment: Kubernetes env and envFrom over the linked image's ENV

    def env_lookup(self, csub: str | None, var: str, *, case_insensitive: bool = False) -> _EnvVal | None:
        """How `var` reaches the process. None: unset and not replaceable from outside the repository."""
        if csub is None:
            return _EnvVal(None, False, None, None, [], "unknown")
        model = self.dep.env_models.get(csub)
        if model is None:
            return _EnvVal(None, False, None, None, [], "unknown")  # no environment model: what the container sees is unknown
        names = [var]
        docker = self._docker_env(csub)
        if case_insensitive:
            names = sorted({n for n in [*model.explicit, *docker] if n.upper() == var.upper()} | {var})
        for n in names:
            r = model.resolve(n)
            if r is not None:
                if r.status is FactStatus.UNRESOLVED or r.value is None or "$(" in str(r.value):
                    return _EnvVal(None, False, r.source, r.via_fact, list(r.exposure), "env")
                return _EnvVal(str(r.value), True, r.source, r.via_fact, list(r.exposure), "env")
        for n in names:
            d = docker.get(n)
            if d is not None:
                value, line, path, unresolved = d
                expo = model.exposure_for(n)
                if unresolved:
                    return _EnvVal(None, False, (path, line), None, expo, "docker_env")
                return _EnvVal(value, True, (path, line), None, expo, "docker_env")
        expo = model.exposure_for(var)
        if expo:
            return _EnvVal(None, False, None, None, expo, "external")
        return None

    def _docker_env(self, csub: str) -> dict[str, tuple[str, int, str, bool]]:
        if csub in self._docker_cache:
            return self._docker_cache[csub]
        out: dict[str, tuple[str, int, str, bool]] = {}
        link = self.dep.container_links.get(csub)
        dpath = self.dep.dockerfile_for_link.get(link.link_id) if link is not None else None
        model = self.dep.dockerfiles.get(dpath) if dpath else None
        target = model.default_target if model is not None else None
        if target is not None and dpath is not None:
            for k, v in target.env.items():
                out[k] = (str(v.value), v.line, dpath, v.unresolved is not None)
        self._docker_cache[csub] = out
        return out

    # -- containers: ASGI app, routes, server settings

    def container(self, csub: str, link: Linkage) -> None:
        tokens = self._command(csub)
        if tokens is None:
            return
        words = [t[0] for t in tokens]
        launcher, start = _launcher(words)
        cmd_fid = next((f.fact_id for f in self.dep.facts.facts() if f.kind == "k8s.container.effective_command" and f.subject.key == csub), None)
        if launcher is None:
            return
        app_ref: tuple[str, int] | None = None
        if launcher == "gunicorn":
            app_ref = self._gunicorn(csub, tokens, start, cmd_fid)
        elif launcher == "uvicorn":
            app_ref = self._uvicorn(csub, tokens, start, cmd_fid)
        if app_ref is not None:
            self._app(csub, link, tokens[app_ref[1]] if app_ref[1] >= 0 else tokens[0], app_ref[0])

    def _command(self, csub: str) -> list[tuple[str, str, int, FactStatus]] | None:
        k8s = [(t, p, ln, FactStatus.DECLARED) for t, p, ln in self.dep.container_args.get(csub, [])]
        cmd_set, args_set = self.dep.container_cmd_set.get(csub, (bool(k8s), False))
        if not cmd_set:
            image = self.dep.container_image_cmd.get(csub)
            if image is None:
                return k8s or None
            ep, cm, _e, _c = image
            out = [(t, p, ln, FactStatus.INFERRED) for t, p, ln in ep]
            out += k8s if args_set else ([(t, p, ln, FactStatus.INFERRED) for t, p, ln in cm] if (ep or not k8s) else [])
            if not ep:
                self._cov(csub, "The Dockerfile sets no ENTRYPOINT, so the base image's ENTRYPOINT is unknown; the Python server settings "
                                "read from CMD assume it runs CMD as the command.")
            k8s = out
        if any("$(" in t for t, *_r in k8s):
            self._cov(csub, "The container command uses $(VAR) expansion; the Python server settings in it are not read.")
            return None
        if k8s and k8s[0][0].rsplit("/", 1)[-1] in shellcmd.SHELLS:
            inv = shellcmd.invocation([t for t, *_r in k8s], 0)
            if inv.opaque is None and inv.script is not None:

                def lookup(var: str) -> tuple[str | None, bool]:
                    v = self.env_lookup(csub, var)
                    if v is None:
                        return None, True
                    return (v.value, True) if v.known and not v.exposure else (None, False)

                positional = k8s[inv.script_index + 1:]
                parsed = shellcmd.expand(inv.script, [t for t, *_r in positional], lookup, brace=inv.shell in shellcmd.BRACE_SHELLS)
                if parsed.opaque is None and not any(w.unknown for w in parsed.words):
                    _t, p, ln, st = k8s[inv.script_index]
                    return [(w.text, p if w.origin < 0 else positional[w.origin][1], ln if w.origin < 0 else positional[w.origin][2], st)
                            for w in parsed.words]
            self._cov(csub, "The container runs a shell command the scanner does not model; Python server settings in it are not read.")
            return None
        return k8s

    def _server_setting(self, csub: str, name: str, value: Any, src: tuple[str, int] | None, status: FactStatus, cmd_fid: str | None,
                        precedence: str, unit: str | None, *, exposure: list[ExposureSource] | None = None, via_fact: str | None = None) -> None:
        inputs = [via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid and precedence.endswith(".cli") else []
        if via_fact:
            inputs.append(via(R.ENV_ATTACHMENT, via_fact))
        self.fb.add(f"{precedence.split('.')[0]}.setting", f"{csub}#{name}", value, status, unit=unit, source=ref(*src) if src else None,
                    inputs=inputs, precedence=precedence, exposure=OverrideExposure.ENV_POSSIBLE if exposure else OverrideExposure.NONE,
                    exposure_sources=_dedupe(exposure or []))

    def _unresolved_setting(self, server: str, csub: str, name: str, reason: str, cmd_fid: str | None) -> None:
        self.fb.add(f"{server}.setting", f"{csub}#{name}", None, FactStatus.UNRESOLVED, inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [],
                    unresolved_reason=reason)

    def _gunicorn(self, csub: str, tokens: list[tuple[str, str, int, FactStatus]], start: int, cmd_fid: str | None) -> tuple[str, int] | None:
        names = ("bind", "workers", "worker_class", "timeout", "graceful_timeout")
        units = {"timeout": "s", "graceful_timeout": "s"}
        cli = _parse_cli("gunicorn", [t[0] for t in tokens[start:]])
        if cli.opaque is not None:
            self._cov(csub, f"gunicorn command line: {cli.opaque}; its settings and application are not read.")
            for n in names:
                self._unresolved_setting("gunicorn", csub, n, f"the gunicorn command line is not fully understood ({cli.opaque})", cmd_fid)
            return None
        env_args = self.env_lookup(csub, "GUNICORN_CMD_ARGS")
        env_cli: _Cli | None = None
        if env_args is not None and env_args.known and env_args.value is not None:
            try:
                env_cli = _parse_cli("gunicorn", shlex.split(env_args.value), positional=False)
            except ValueError:
                env_cli = _Cli(opaque="GUNICORN_CMD_ARGS cannot be split into words")
        config = "config" in cli.values or (env_cli is not None and "config" in env_cli.values)
        default_conf = any(f.path.rsplit("/", 1)[-1] == "gunicorn.conf.py" and (f.path.startswith(self.index.root.rstrip("/") + "/")
                                                                                   or self.index.root == ".") for f in self.ws.files)
        for n in names:
            if n in cli.values:
                raw, i = cli.values[n][-1]
                vals = [v for v, _ in cli.values[n]]
                value: Any = (vals[0] if len(vals) == 1 else vals) if n == "bind" else _int_or(raw, n in ("workers", "timeout", "graceful_timeout"))
                t = tokens[start + i]
                self._server_setting(csub, n, value, (t[1], t[2]), t[3], cmd_fid, "gunicorn.cli", units.get(n))
                continue
            if env_args is not None and not env_args.known and env_args.layer != "external":
                self._unresolved_setting("gunicorn", csub, n, "GUNICORN_CMD_ARGS is set from a value the repository does not show", cmd_fid)
                continue
            if env_cli is not None and env_cli.opaque is not None:
                self._unresolved_setting("gunicorn", csub, n, f"GUNICORN_CMD_ARGS is not understood ({env_cli.opaque})", cmd_fid)
                continue
            if env_cli is not None and n in env_cli.values:
                assert env_args is not None
                vals = [v for v, _ in env_cli.values[n]]
                value = (vals[0] if len(vals) == 1 else vals) if n == "bind" else _int_or(vals[-1], n in ("workers", "timeout", "graceful_timeout"))
                self._server_setting(csub, n, value, env_args.source, FactStatus.DECLARED if env_args.layer == "env" else FactStatus.INFERRED,
                                     cmd_fid, "gunicorn.env", units.get(n), exposure=env_args.exposure, via_fact=env_args.via)
                continue
            if config or default_conf:
                why = "a gunicorn configuration file is read" + ("" if config else " (gunicorn.conf.py in the repository, loaded from the working directory)")
                self._unresolved_setting("gunicorn", csub, n, f"{why}; the scanner does not read it", cmd_fid)
                continue
            env_dep = {"workers": "WEB_CONCURRENCY", "bind": "PORT"}.get(n)
            dep_val = self.env_lookup(csub, env_dep) if env_dep else None
            if env_dep is not None and dep_val is not None and (dep_val.known or dep_val.layer != "external"):
                self._unresolved_setting("gunicorn", csub, n, f"the default depends on {env_dep}, which the container sets; the scanner does not derive it",
                                         cmd_fid)
                continue
            model = self.dep.env_models.get(csub)
            expo_srcs = [x.model_copy(update={"variables": ["GUNICORN_CMD_ARGS"]}) for x in (env_args.exposure if env_args else [])]
            if env_dep is not None and dep_val is not None:
                expo_srcs += [x.model_copy(update={"variables": [env_dep]}) for x in dep_val.exposure]
            expo = OverrideExposure.ENV_POSSIBLE if (expo_srcs or model is None) else OverrideExposure.NONE
            self._default("gunicorn.setting", f"{csub}#{n}", f"gunicorn.{n}", "gunicorn", (tokens[0][1], tokens[0][2]),
                          extra_inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [], exposure=expo, exposure_sources=_dedupe(expo_srcs),
                          precedence="gunicorn.default")
        if "chdir" in cli.values or "pythonpath" in cli.values:
            self._cov(csub, "gunicorn changes the working directory or the import path (--chdir, --pythonpath); the application module is not resolved.")
            return None
        if "raw_env" in cli.values:
            self.env_changed.add(csub)
            self._cov(csub, "gunicorn sets environment variables for the application (--env); settings read from the environment may change.")
        if cli.app is None:
            return None
        return cli.app[0], start + cli.app[1]

    def _uvicorn(self, csub: str, tokens: list[tuple[str, str, int, FactStatus]], start: int, cmd_fid: str | None) -> tuple[str, int] | None:
        names = ("host", "port", "timeout_graceful_shutdown", "reload")
        cli = _parse_cli("uvicorn", [t[0] for t in tokens[start:]])
        if cli.opaque is not None:
            self._cov(csub, f"uvicorn command line: {cli.opaque}; its settings and application are not read.")
            for n in names:
                self._unresolved_setting("uvicorn", csub, n, f"the uvicorn command line is not fully understood ({cli.opaque})", cmd_fid)
            return None
        socket = next((n for n in ("uds", "fd") if n in cli.values), None)
        for n in names:
            if socket and n in ("host", "port"):
                self._unresolved_setting("uvicorn", csub, n, f"uvicorn binds the --{socket} socket, so host and port are not used", cmd_fid)
                continue
            if n in cli.values:
                raw, i = cli.values[n][-1]
                t = tokens[start + i]
                value: Any = True if raw is None else _int_or(raw, n in ("port", "timeout_graceful_shutdown"))
                self._server_setting(csub, n, value, (t[1], t[2]), t[3], cmd_fid, "uvicorn.cli", "s" if n == "timeout_graceful_shutdown" else None)
                continue
            var = f"UVICORN_{n.upper()}"  # click auto_envvar_prefix
            ev = self.env_lookup(csub, var)
            if ev is not None and ev.layer != "external":
                if not ev.known or ev.value is None:
                    self._unresolved_setting("uvicorn", csub, n, f"{var} is set from a value the repository does not show", cmd_fid)
                    continue
                value = _click_value(ev.value, n)
                if value is _BAD:
                    self._unresolved_setting("uvicorn", csub, n, f"{var}={ev.value!r} is not a value uvicorn accepts", cmd_fid)
                    continue
                self._server_setting(csub, n, value, ev.source, FactStatus.DECLARED if ev.layer == "env" else FactStatus.INFERRED, cmd_fid,
                                     "uvicorn.env", "s" if n == "timeout_graceful_shutdown" else None, exposure=ev.exposure, via_fact=ev.via)
                continue
            if n == "reload":
                continue
            model = self.dep.env_models.get(csub)
            srcs = [x.model_copy(update={"variables": [var]}) for x in (ev.exposure if ev else [])]
            expo = OverrideExposure.ENV_POSSIBLE if (srcs or model is None) else OverrideExposure.NONE
            self._default("uvicorn.setting", f"{csub}#{n}", f"uvicorn.{n}", "uvicorn", (tokens[0][1], tokens[0][2]),
                          extra_inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [], exposure=expo, exposure_sources=srcs,
                          precedence="uvicorn.default")
        if "env_file" in cli.values:
            self.env_changed.add(csub)
            self._cov(csub, "uvicorn loads an env file (--env-file); settings read from the environment may change.")
        if "factory" in cli.values:
            self._cov(csub, "uvicorn --factory builds the application by calling a function; its routes are not read.")
            return None
        app_dir = cli.values.get("app_dir", [(".", -1)])[-1][0]
        if app_dir not in (".", "", "./"):
            self._cov(csub, f"uvicorn --app-dir {app_dir} changes where the application module is imported from; the module is not resolved.")
            return None
        if cli.app is None:
            ev = self.env_lookup(csub, "UVICORN_APP")
            if ev is not None and ev.known and ev.value:
                return ev.value, -1
            return None
        return cli.app[0], start + cli.app[1]

    def _app(self, csub: str, link: Linkage, token: tuple[str, str, int, FactStatus], ref_text: str) -> None:
        m = re.fullmatch(r"([A-Za-z_][\w.]*):([A-Za-z_]\w*)", ref_text)
        if not m:
            return
        mod_name, attr = m.group(1), m.group(2)
        subject = f"python:{mod_name}:{attr}"
        inputs = [loc(R.REFERENCE, token[1], token[2])]
        mod = self.index.get(mod_name)
        value_node = self.index.top_assign(mod, attr) if mod is not None else None
        if mod is None or value_node is None:
            self.fb.add("python.asgi_app", subject, None, FactStatus.UNRESOLVED, inputs=inputs, link_ids=[link.link_id],
                        unresolved_reason=f"module {mod_name} or its attribute {attr} is not found in the repository")
            return
        line = next(n.lineno for n in mod.tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is value_node)
        is_fastapi = isinstance(value_node, ast.Call) and _is(self.index, mod, value_node.func, "fastapi.FastAPI", "fastapi.applications.FastAPI")
        if not is_fastapi:
            self.fb.add("python.asgi_app", subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, line), inputs=inputs, link_ids=[link.link_id],
                        unresolved_reason="the application object is not built by a direct FastAPI(...) call")
            return
        self.fb.add("python.asgi_app", subject, {"framework": "fastapi", "module": mod_name, "attribute": attr}, FactStatus.DECLARED,
                    source=ref(mod.path, line), inputs=inputs, link_ids=[link.link_id])
        call: ast.Call = value_node  # type: ignore[assignment]
        self._debug(mod, attr, call, line, subject)
        _Routes(self, mod, attr, call, subject).collect()

    def _debug(self, mod: PyModule, attr: str, call: ast.Call, line: int, subject: str) -> None:
        later = [n for n in mod.tree.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == attr and t.attr == "debug" for t in n.targets)]
        node: ast.expr | None = later[-1].value if later else (_kw(call, "debug").value if _kw(call, "debug") else None)  # type: ignore[union-attr]
        if node is None:
            if any(k.arg is None for k in call.keywords):
                self.fb.add("fastapi.debug", subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, line),
                            unresolved_reason="FastAPI(**kwargs) may set debug")
                return
            self._default("fastapi.debug", subject, "fastapi.debug", "fastapi", (mod.path, line))
            return
        ok, v = _literal(node)
        if ok and isinstance(v, bool):
            self.fb.add("fastapi.debug", subject, v, FactStatus.DECLARED, source=ref(mod.path, node.lineno))
        else:
            self.fb.add("fastapi.debug", subject, None, FactStatus.UNRESOLVED, source=ref(mod.path, node.lineno),
                        unresolved_reason=f"debug={ast.unparse(node)[:60]} is not a literal")

    # -- route handlers: deadlines and calls to retried functions

    def handlers(self) -> None:
        for dotted, route_fid in sorted(self.routes.items()):
            sp = self.index.split(dotted)
            if sp is None:
                continue
            mod, name = sp
            fn = self.index.top_def(mod, name)
            if fn is None:
                continue
            subject = f"python:{dotted}"
            spans = self._deadline(mod, fn, subject)
            calls: list[tuple[str, int, tuple[int, int, str] | None]] = []
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    d = self.index.dotted(mod, node.func)
                    if d is not None and d in self.retry_funcs and all(c[0] != d for c in calls):
                        around = [sp for sp in spans if sp[0] <= node.lineno <= sp[1]]
                        inner = max(around, key=lambda sp: sp[0]) if around else None  # the innermost deadline applies first
                        calls.append((d, node.lineno, inner))
            for k, (d, line, inner) in enumerate(calls):
                inputs = [via(R.REFERENCE, route_fid)] + ([via(R.REFERENCE, inner[2])] if inner else [])
                self.fb.add("python.call_reaches", subject, {"callee": self.retry_funcs[d], "within_deadline": subject if inner else None},
                            FactStatus.DECLARED, source=ref(mod.path, line), scope={} if k == 0 else {"callee": d}, inputs=inputs)

    def _deadline(self, mod: PyModule, fn: ast.FunctionDef | ast.AsyncFunctionDef, subject: str) -> list[tuple[int, int, str]]:
        """One deadline fact per `with` block: (first line, last line, fact id)."""
        spans: list[tuple[int, int, str]] = []
        for node in sorted((n for n in ast.walk(fn) if isinstance(n, (ast.With, ast.AsyncWith))), key=lambda n: n.lineno):
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call) and _is(self.index, mod, ce.func, "asyncio.timeout", "anyio.fail_after", "asyncio.timeouts.timeout"):
                    mech = self.index.dotted(mod, ce.func) or "asyncio.timeout"
                    mech = "asyncio.timeout" if mech.startswith("asyncio") else "anyio.fail_after"
                    arg = ce.args[0] if ce.args else (_kw(ce, "delay").value if _kw(ce, "delay") else None)  # type: ignore[union-attr]
                    end = getattr(node, "end_lineno", node.lineno) or node.lineno
                    fid = self._deadline_fact(mod, subject, mech, arg, node.lineno, first=not spans)
                    spans.append((node.lineno, end, fid))
        return spans

    def _add_deadline(self, scope: dict[str, str], subject: str, value: Any, status: FactStatus, **kw: Any) -> str:
        return self.fb.add("python.deadline", subject, value, status, scope=scope, **kw)

    def _deadline_fact(self, mod: PyModule, subject: str, mech: str, arg: ast.expr | None, line: int, *, first: bool = True) -> str:
        src = ref(mod.path, line)
        scope = {} if first else {"line": str(line)}
        ok, v = _literal(arg)
        if ok and isinstance(v, (int, float)) and not isinstance(v, bool):
            return self._add_deadline(scope, subject, {"mechanism": mech, "seconds": float(v)}, FactStatus.DECLARED, unit="s", source=src)
        if ok and v is None:
            return self._add_deadline(scope, subject, {"mechanism": mech, "seconds": None}, FactStatus.DECLARED, unit="s", source=src)
        if isinstance(arg, ast.Attribute):
            inst = self.index.dotted(mod, arg.value)
            cls = self.instances.get(inst or "")
            setting = self.settings_fields.get(f"{cls}.{arg.attr}") if cls else None
            if setting is not None:
                inputs = [via(R.VALUE_SOURCE, setting.fid)]
                expo = setting.exposure
                srcs = [ExposureSource(via="inherited", from_fact_ids=[setting.fid])] if expo is not OverrideExposure.NONE else []
                if setting.known and isinstance(setting.value, (int, float)) and not isinstance(setting.value, bool):
                    return self._add_deadline(scope, subject, {"mechanism": mech, "seconds": float(setting.value)}, FactStatus.DECLARED,
                                       unit="s", source=src, inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=setting.link_ids)
                return self._add_deadline(scope, subject, None, FactStatus.UNRESOLVED, source=src, inputs=inputs, exposure=expo,
                                   exposure_sources=srcs, link_ids=setting.link_ids, unresolved_reason="the settings value it uses is unresolved")
        return self._add_deadline(scope, subject, None, FactStatus.UNRESOLVED, source=src,
                           unresolved_reason=f"the deadline {ast.unparse(arg)[:60] if arg is not None else '(none)'} is not a literal or a settings field")


def _dedupe(srcs: list[ExposureSource]) -> list[ExposureSource]:
    out: list[ExposureSource] = []
    for s in srcs:
        same = next((x for x in out if (x.via, x.object) == (s.via, s.object)), None)
        if same is None:
            out.append(s)
        else:
            out[out.index(same)] = same.model_copy(update={"variables": [*same.variables, *[v for v in s.variables if v not in same.variables]]})
    return out


def _coerce(value: Any, annotation: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        if annotation in ("float",):
            return float(value)
        if annotation in ("int",):
            return int(value)
    except ValueError:
        return None
    return value


def _launcher(words: list[str]) -> tuple[str | None, int]:
    """Which ASGI server the command starts and the index of its first argument."""
    for i, w in enumerate(words):
        base = w.rsplit("/", 1)[-1]
        if base in ("gunicorn", "uvicorn"):
            return base, i + 1
        if re.fullmatch(r"python[\d.]*", base) and i + 2 < len(words) and words[i + 1] == "-m" and words[i + 2] in ("gunicorn", "uvicorn"):
            return words[i + 2], i + 3
        if base in ("exec", "env", "tini", "dumb-init", "--") or (i == 0 and base.startswith("python")):
            continue
        if not w.startswith("-") and base not in ("exec",):
            return None, 0
    return None, 0


class _Routes:
    """Routes of one FastAPI application, collected from literal decorators and include_router calls at module level."""

    def __init__(self, root: _Root, mod: PyModule, attr: str, call: ast.Call, app_subject: str) -> None:
        self.r, self.mod, self.attr, self.call, self.app_subject = root, mod, attr, call, app_subject
        self.incomplete: list[str] = []
        self.count = 0
        self.walked: set[tuple[str, str]] = set()
        version = root.versions.get("fastapi", (None, ""))[0]
        parts = [int(x) for x in re.findall(r"\d+", version or "")[:3]]
        # from 0.137.0 an included router stays live; before it, include_router copies the routes that exist at that moment
        self.live_includes = len(parts) == 3 and tuple(parts) >= (0, 137, 0)

    def collect(self) -> None:
        if _kw(self.call, "root_path") is not None:
            self.incomplete.append(f"{self.mod.path}:{self.call.lineno}: root_path")
        if _kw(self.call, "routes") is not None:
            self.incomplete.append(f"{self.mod.path}:{self.call.lineno}: routes=")
        self._object(self.mod, self.attr, "", [], 0)
        self._foreign()
        complete = not self.incomplete
        # the built-in documentation routes (docs_url, redoc_url, openapi_url) are not counted or listed
        self.r.fb.add("fastapi.route_table", self.app_subject, {"complete": complete, "routes": self.count, "documentation_routes": "not listed"},
                      FactStatus.DECLARED, source=ref(self.mod.path, self.call.lineno))
        if not complete:
            shown = "; ".join(self.incomplete[:5])
            self.r._cov(self.app_subject, f"The route table may be incomplete ({shown}); a path not listed may still be served.")

    def _foreign(self) -> None:
        """Routes or routers registered on the application or one of its routers from another module (which the scanner does not know
        is imported at runtime) make the table incomplete."""
        for mname, oname in sorted(self.walked):
            for other in self.r.index.candidates(oname):
                if other.name == mname:
                    continue
                for node in ast.walk(other.tree):
                    target: ast.expr | None = None
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                            *HTTP_METHODS, "api_route", "route", "include_router", "add_api_route", "add_route", "mount", "websocket"):
                        target = node.func.value
                    if target is not None and self.r.index.dotted(other, target) == f"{mname}.{oname}":
                        self.incomplete.append(f"{other.path}:{getattr(node, 'lineno', 0)}: registered on {oname} from another module")
                        break

    def _object(self, mod: PyModule, name: str, prefix: str, refs: list[tuple[str, int]], depth: int) -> None:
        if depth > 10:
            self.incomplete.append(f"{mod.path}: routers nest too deeply")
            return
        self.walked.add((mod.name, name))
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Attribute) and node.attr == "router" and isinstance(node.value, ast.Name) and node.value.id == name:
                self.incomplete.append(f"{mod.path}:{node.lineno}: {name}.router is used directly")
            # decorators: @name.get("/p")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name) \
                            and dec.func.value.id == name and dec.func.attr in (*HTTP_METHODS, "api_route", "route", "websocket"):
                        if node not in mod.tree.body:
                            self.incomplete.append(f"{mod.path}:{dec.lineno}: route defined inside a function or class")
                            continue
                        self._route(mod, dec, dec.func.attr, prefix, refs, f"{mod.name}.{node.name}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == name:
                attr = node.func.attr
                top = any(isinstance(s, ast.Expr) and s.value is node for s in mod.tree.body)
                if attr == "include_router":
                    if not top:
                        self.incomplete.append(f"{mod.path}:{node.lineno}: include_router inside a function, loop or condition")
                        continue
                    self._include(mod, node, prefix, refs, depth)
                elif attr in ("add_api_route", "add_route", "mount", "add_websocket_route", "host"):
                    self.incomplete.append(f"{mod.path}:{node.lineno}: {attr}()")
                elif attr in (*HTTP_METHODS, "api_route") and not top and not any(
                        isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in f.decorator_list for f in ast.walk(mod.tree)):
                    self.incomplete.append(f"{mod.path}:{node.lineno}: {attr}() called outside a decorator")

    def _include(self, mod: PyModule, call: ast.Call, prefix: str, refs: list[tuple[str, int]], depth: int) -> None:
        target = call.args[0] if call.args else (_kw(call, "router").value if _kw(call, "router") else None)  # type: ignore[union-attr]
        inc_prefix = ""
        pk = _kw(call, "prefix")
        if pk is not None:
            ok, v = _literal(pk.value)
            if not ok or not isinstance(v, str):
                self.incomplete.append(f"{mod.path}:{call.lineno}: include_router prefix is not a literal")
                return
            inc_prefix = v
        dotted = self.r.index.dotted(mod, target) if target is not None else None
        obj = self.r.index.resolve_object(dotted) if dotted else None
        if obj is None or not isinstance(obj[2], ast.Call) or not _is(self.r.index, obj[0], obj[2].func, "fastapi.APIRouter", "fastapi.routing.APIRouter"):
            self.incomplete.append(f"{mod.path}:{call.lineno}: include_router of an object the scanner cannot follow")
            return
        rmod, rname, rcall = obj
        if not self.live_includes and rmod.name == mod.name:
            late = [n for n in ast.walk(mod.tree) if getattr(n, "lineno", 0) > call.lineno and (
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == rname)]
            if late:
                self.incomplete.append(f"{mod.path}:{getattr(late[0], 'lineno', 0)}: {rname} changes after it is included "
                                       "(fastapi before 0.137 copies routes at include time)")
        router_prefix = ""
        rk = _kw(rcall, "prefix")  # type: ignore[arg-type]
        if rk is not None:
            ok, v = _literal(rk.value)
            if not ok or not isinstance(v, str):
                self.incomplete.append(f"{rmod.path}:{rcall.lineno}: APIRouter prefix is not a literal")
                return
            router_prefix = v
        if _kw(rcall, "routes") is not None:  # type: ignore[arg-type]
            self.incomplete.append(f"{rmod.path}:{rcall.lineno}: APIRouter(routes=...)")
        self._object(rmod, rname, prefix + inc_prefix + router_prefix, [*refs, (mod.path, call.lineno)], depth + 1)

    def _route(self, mod: PyModule, dec: ast.Call, verb: str, prefix: str, refs: list[tuple[str, int]], endpoint: str) -> None:
        path_node = dec.args[0] if dec.args else (_kw(dec, "path").value if _kw(dec, "path") else None)  # type: ignore[union-attr]
        ok, path = _literal(path_node)
        if not ok or not isinstance(path, str):
            self.incomplete.append(f"{mod.path}:{dec.lineno}: route path is not a literal")
            return
        if verb == "websocket":
            return
        if verb in ("api_route", "route"):
            mk = _kw(dec, "methods")
            ok, methods = _literal(mk.value) if mk is not None else (True, ["GET"])
            if not ok or not isinstance(methods, (list, tuple, set)):
                self.incomplete.append(f"{mod.path}:{dec.lineno}: route methods are not a literal")
                return
            verbs = sorted({str(m).upper() for m in methods})
        else:
            verbs = [verb.upper()]
        full = prefix + path
        for v in verbs:
            fid = self.r.fb.add("fastapi.route", f"{self.app_subject}#{v}:{full}", {"method": v, "path": full}, FactStatus.DECLARED,
                                source=ref(mod.path, dec.lineno), inputs=[loc(R.REFERENCE, p, ln) for p, ln in refs])
            self.count += 1
            self.r.routes.setdefault(endpoint, fid)


__all__ = ["PythonResult", "py_default", "resolve_python"]

