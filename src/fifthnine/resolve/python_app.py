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
        run.settings()
        run.retries()
        for csub, link in contexts:
            run.container(csub, link)
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

    def _settings_config(self, mod: PyModule, cls: ast.ClassDef) -> tuple[str | None, bool]:
        """(env_prefix, env_file configured). None prefix: not a literal."""
        prefix: str | None = ""
        env_file = False
        for node in cls.body:
            call: ast.Call | None = None
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
                    elif k.arg in ("env_file", "secrets_dir", "case_sensitive") or k.arg is None:
                        env_file = True
            if isinstance(node, ast.ClassDef) and node.name == "Config":
                for sub in node.body:
                    if isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Name):
                        if sub.targets[0].id == "env_prefix":
                            ok, v = _literal(sub.value)
                            prefix = v if ok and isinstance(v, str) else None
                        elif sub.targets[0].id in ("env_file", "secrets_dir", "case_sensitive"):
                            env_file = True
        return prefix, env_file

    def _settings_field(self, mod: PyModule, cls: ast.ClassDef, name: str, node: ast.AnnAssign, prefix: str | None, special: bool) -> None:
        subject = f"python:{mod.name}.{cls.name}#{name}"
        value_node: ast.expr | None = node.value
        env: str | None = (prefix + name).upper() if prefix is not None else None
        required = value_node is None
        default_ok, default = (True, None) if required else _literal(value_node)
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
            self._cov(f"{mod.path}:{cls.lineno}", f"{cls.name} reads an env file, secrets directory or a case-sensitive configuration; "
                      "values that depend on its fields are unresolved.")
        effective = env_value if env_value is not None else (None if required else default)
        self.settings_fields[f"{mod.name}.{cls.name}.{name}"] = _Setting(
            fid, _coerce(effective, ast.unparse(node.annotation)), known and not required and env_value is None or (env_value is not None),
            exposure, link_ids)

    def _settings_exposure(self, env: str | None) -> tuple[OverrideExposure, list[ExposureSource], list[str], str | None]:
        """Exposure of an env-bound field across the containers that run this root, and an in-repository env value if one sets it
        (pydantic-settings matches names case-insensitively by default)."""
        models = self._env_models()
        link_ids = [link.link_id for _c, _m, link in models]
        if not models:
            return OverrideExposure.ENV_POSSIBLE, [], [], None  # no container is linked: what the environment sets is unknown
        sources: list[ExposureSource] = []
        env_value: str | None = None
        for _csub, model, _link in models:
            if model is None or env is None:
                return OverrideExposure.ENV_POSSIBLE, sources, link_ids, None
            names = [n for n in model.explicit if n.upper() == env] or [env]
            for n in names:
                srcs = model.exposure_for(n)
                for s in srcs:
                    s2 = s.model_copy(update={"variables": [env]})
                    if all((x.via, x.object) != (s2.via, s2.object) for x in sources):
                        sources.append(s2)
                r = model.resolve(n)
                if r is not None and r.status is not FactStatus.UNRESOLVED and r.value is not None and len(models) == 1:
                    env_value = str(r.value)
                elif r is not None and r.status is FactStatus.UNRESOLVED:
                    sources.append(ExposureSource(via="valueFrom", object=f"{_csub}#{n}", variables=[n]))
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
                self.fb.add("python.retry.client", subject, {"client": f"python:{cdotted}"}, FactStatus.DECLARED,
                            source=ref(mod.path, node.lineno), scope=scope, inputs=[via(R.REFERENCE, tfid)])

    def _client_timeout(self, mod: PyModule, name: str, call: ast.Call) -> str:
        dotted = f"{mod.name}.{name}"
        if dotted in self.clients:
            return self.clients[dotted]
        subject = f"python:{dotted}"
        kind = "python.http_client.timeout"
        kw = _kw(call, "timeout")
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
            self._app(csub, link, tokens[app_ref[1]], app_ref[0])

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
            k8s = out
        if any("$(" in t for t, *_r in k8s):
            self._cov(csub, "The container command uses $(VAR) expansion; the Python server settings in it are not read.")
            return None
        if k8s and k8s[0][0].rsplit("/", 1)[-1] in shellcmd.SHELLS:
            if len(k8s) >= 3 and re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", k8s[1][0]):
                model = self.dep.env_models.get(csub)

                def lookup(var: str) -> tuple[str | None, bool]:
                    if model is None or model.exposure_for(var):
                        return None, False
                    r = model.resolve(var)
                    if r is None:
                        return None, True
                    return (str(r.value), True) if r.status is not FactStatus.UNRESOLVED and r.value is not None else (None, False)

                parsed = shellcmd.expand(k8s[2][0], [t for t, *_r in k8s[3:]], lookup)
                if parsed.opaque is None and not any(w.unknown for w in parsed.words):
                    _t, p, ln, st = k8s[2]
                    return [(w.text, p if w.origin < 0 else k8s[3 + w.origin][1], ln if w.origin < 0 else k8s[3 + w.origin][2],
                             FactStatus.INFERRED if st is FactStatus.INFERRED else st) for w in parsed.words]
            self._cov(csub, "The container runs a shell command the scanner does not model; Python server settings in it are not read.")
            return None
        return k8s

    def _server_setting(self, csub: str, name: str, value: Any, src: tuple[str, int], status: FactStatus, cmd_fid: str | None,
                        precedence: str, unit: str | None) -> None:
        inputs = [via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else []
        self.fb.add(f"{precedence.split('.')[0]}.setting", f"{csub}#{name}", value, status, unit=unit, source=ref(*src), inputs=inputs,
                    precedence=precedence)

    def _gunicorn(self, csub: str, tokens: list[tuple[str, str, int, FactStatus]], start: int, cmd_fid: str | None) -> tuple[str, int] | None:
        opts = {"-w": "workers", "--workers": "workers", "-k": "worker_class", "--worker-class": "worker_class", "-b": "bind", "--bind": "bind",
                "-t": "timeout", "--timeout": "timeout", "--graceful-timeout": "graceful_timeout", "-c": "config", "--config": "config"}
        units = {"timeout": "s", "graceful_timeout": "s"}
        found: dict[str, tuple[Any, tuple[str, int], FactStatus]] = {}
        binds: list[tuple[str, tuple[str, int], FactStatus]] = []
        app: tuple[str, int] | None = None
        i = start
        unknown_flag = False
        while i < len(tokens):
            w, p, ln, st = tokens[i]
            name: str | None = None
            raw_value: str | None = None
            if w.startswith("--") and "=" in w:
                flag, _, raw_value = w.partition("=")
                name = opts.get(flag)
            elif w in opts and i + 1 < len(tokens):
                name, raw_value = opts[w], tokens[i + 1][0]
                i += 1
            elif re.fullmatch(r"-[wkbt]\S+", w):
                name, raw_value = opts[w[:2]], w[2:]
            elif w.startswith("-"):
                unknown_flag = unknown_flag or w in ("--paste", "--env", "-e")
                i += 1
                continue
            elif app is None:
                app = (w, i)
            if name is not None and raw_value is not None:
                if name == "bind":
                    binds.append((raw_value, (p, ln), st))
                else:
                    found[name] = (raw_value, (p, ln), st)
            i += 1
        if "config" in found:
            self._cov(csub, "gunicorn reads a configuration file (-c); settings it sets are not read, so settings not on the command line are unresolved.")
        model = self.dep.env_models.get(csub)
        cmd_args_expo = model.exposure_for("GUNICORN_CMD_ARGS") if model is not None else []
        explicit_cmd_args = model.resolve("GUNICORN_CMD_ARGS") if model is not None else None
        for name in ("bind", "workers", "worker_class", "timeout", "graceful_timeout"):
            if name == "bind" and binds:
                value: Any = binds[0][0] if len(binds) == 1 else [b[0] for b in binds]
                self._server_setting(csub, name, value, binds[0][1], binds[0][2], cmd_fid, "gunicorn.cli", None)
                continue
            if name in found:
                raw, src, st = found[name]
                value = int(raw) if name in ("workers", "timeout", "graceful_timeout") and str(raw).lstrip("-").isdigit() else raw
                self._server_setting(csub, name, value, src, st, cmd_fid, "gunicorn.cli", units.get(name))
                continue
            subject = f"{csub}#{name}"
            if "config" in found or explicit_cmd_args is not None or unknown_flag:
                self.fb.add("gunicorn.setting", subject, None, FactStatus.UNRESOLVED, inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [],
                            unresolved_reason="a configuration file or GUNICORN_CMD_ARGS in the repository may set it, and the scanner does not read them")
                continue
            expo_srcs = [s.model_copy(update={"variables": ["GUNICORN_CMD_ARGS"]}) for s in cmd_args_expo]
            env_dep = {"workers": "WEB_CONCURRENCY", "bind": "PORT"}.get(name)
            if env_dep is not None and model is not None:
                if model.resolve(env_dep) is not None:
                    self.fb.add("gunicorn.setting", subject, None, FactStatus.UNRESOLVED, inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [],
                                unresolved_reason=f"the default depends on {env_dep}, which the container sets; the scanner does not derive it")
                    continue
                expo_srcs += [s.model_copy(update={"variables": [env_dep]}) for s in model.exposure_for(env_dep)]
            expo = OverrideExposure.ENV_POSSIBLE if (expo_srcs or model is None) else OverrideExposure.NONE
            absence = (tokens[0][1], tokens[0][2])
            self._default("gunicorn.setting", subject, f"gunicorn.{name}", "gunicorn", absence,
                          extra_inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [], exposure=expo, exposure_sources=_dedupe(expo_srcs),
                          precedence="gunicorn.default")
        return app

    def _uvicorn(self, csub: str, tokens: list[tuple[str, str, int, FactStatus]], start: int, cmd_fid: str | None) -> tuple[str, int] | None:
        opts = {"--host": "host", "--port": "port", "--timeout-graceful-shutdown": "timeout_graceful_shutdown", "--workers": "workers"}
        flags = {"--reload": "reload", "--factory": "factory"}
        found: dict[str, tuple[Any, tuple[str, int], FactStatus]] = {}
        app: tuple[str, int] | None = None
        i = start
        while i < len(tokens):
            w, p, ln, st = tokens[i]
            if w.startswith("--") and "=" in w and w.partition("=")[0] in opts:
                found[opts[w.partition("=")[0]]] = (w.partition("=")[2], (p, ln), st)
            elif w in opts and i + 1 < len(tokens):
                found[opts[w]] = (tokens[i + 1][0], (p, ln), st)
                i += 1
            elif w in flags:
                found[flags[w]] = (True, (p, ln), st)
            elif w.startswith("-"):
                pass
            elif app is None:
                app = (w, i)
            i += 1
        model = self.dep.env_models.get(csub)
        for name in ("host", "port", "timeout_graceful_shutdown", "reload"):
            subject = f"{csub}#{name}"
            if name in found:
                raw, src, st = found[name]
                value = int(raw) if name in ("port", "timeout_graceful_shutdown") and str(raw).isdigit() else raw
                self._server_setting(csub, name, value, src, st, cmd_fid, "uvicorn.cli", "s" if name == "timeout_graceful_shutdown" else None)
                continue
            var = f"UVICORN_{name.upper()}"  # click auto_envvar_prefix
            if model is not None and model.resolve(var) is not None:
                r = model.resolve(var)
                assert r is not None
                if r.status is FactStatus.UNRESOLVED or r.value is None:
                    self.fb.add("uvicorn.setting", subject, None, FactStatus.UNRESOLVED,
                                unresolved_reason=f"{var} is set from a value the repository does not show")
                else:
                    self.fb.add("uvicorn.setting", subject, r.value, FactStatus.DECLARED, source=ref(*r.source) if r.source else None,
                                inputs=[via(R.ENV_ATTACHMENT, r.via_fact)] if r.via_fact else [], precedence="uvicorn.env",
                                exposure=OverrideExposure.ENV_POSSIBLE if r.exposure else OverrideExposure.NONE, exposure_sources=r.exposure)
                continue
            if name == "reload":
                continue
            srcs = [s.model_copy(update={"variables": [var]}) for s in (model.exposure_for(var) if model is not None else [])]
            expo = OverrideExposure.ENV_POSSIBLE if (srcs or model is None) else OverrideExposure.NONE
            self._default("uvicorn.setting", subject, f"uvicorn.{name}", "uvicorn", (tokens[0][1], tokens[0][2]),
                          extra_inputs=[via(R.VALUE_SOURCE, cmd_fid)] if cmd_fid else [], exposure=expo, exposure_sources=srcs,
                          precedence="uvicorn.default")
        if "factory" in found and app is not None:
            self._cov(csub, "uvicorn --factory builds the application by calling a function; its routes are not read.")
            return None
        return app

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
            deadline_fid, spans = self._deadline(mod, fn, subject)
            calls: list[tuple[str, int, bool]] = []
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    d = self.index.dotted(mod, node.func)
                    if d is not None and d in self.retry_funcs and all(c[0] != d for c in calls):
                        inside = any(a <= node.lineno <= b for a, b in spans)
                        calls.append((d, node.lineno, inside))
            for k, (d, line, inside) in enumerate(calls):
                inputs = [via(R.REFERENCE, route_fid)] + ([via(R.REFERENCE, deadline_fid)] if inside and deadline_fid else [])
                self.fb.add("python.call_reaches", subject, {"callee": self.retry_funcs[d], "within_deadline": subject if inside else None},
                            FactStatus.DECLARED, source=ref(mod.path, line), scope={} if k == 0 else {"callee": d}, inputs=inputs)

    def _deadline(self, mod: PyModule, fn: ast.FunctionDef | ast.AsyncFunctionDef, subject: str) -> tuple[str | None, list[tuple[int, int]]]:
        spans: list[tuple[int, int]] = []
        fid: str | None = None
        for node in ast.walk(fn):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    ce = item.context_expr
                    if isinstance(ce, ast.Call) and _is(self.index, mod, ce.func, "asyncio.timeout", "anyio.fail_after", "asyncio.timeouts.timeout"):
                        mech = self.index.dotted(mod, ce.func) or "asyncio.timeout"
                        mech = "asyncio.timeout" if mech.startswith("asyncio") else "anyio.fail_after"
                        arg = ce.args[0] if ce.args else (_kw(ce, "delay").value if _kw(ce, "delay") else None)  # type: ignore[union-attr]
                        end = getattr(node, "end_lineno", node.lineno) or node.lineno
                        spans.append((node.lineno, end))
                        if fid is None:
                            fid = self._deadline_fact(mod, subject, mech, arg, node.lineno)
        return fid, spans

    def _deadline_fact(self, mod: PyModule, subject: str, mech: str, arg: ast.expr | None, line: int) -> str:
        src = ref(mod.path, line)
        ok, v = _literal(arg)
        if ok and isinstance(v, (int, float)) and not isinstance(v, bool):
            return self.fb.add("python.deadline", subject, {"mechanism": mech, "seconds": float(v)}, FactStatus.DECLARED, unit="s", source=src)
        if ok and v is None:
            return self.fb.add("python.deadline", subject, {"mechanism": mech, "seconds": None}, FactStatus.DECLARED, unit="s", source=src)
        if isinstance(arg, ast.Attribute):
            inst = self.index.dotted(mod, arg.value)
            cls = self.instances.get(inst or "")
            setting = self.settings_fields.get(f"{cls}.{arg.attr}") if cls else None
            if setting is not None:
                inputs = [via(R.VALUE_SOURCE, setting.fid)]
                expo = setting.exposure
                srcs = [ExposureSource(via="inherited", from_fact_ids=[setting.fid])] if expo is not OverrideExposure.NONE else []
                if setting.known and isinstance(setting.value, (int, float)) and not isinstance(setting.value, bool):
                    return self.fb.add("python.deadline", subject, {"mechanism": mech, "seconds": float(setting.value)}, FactStatus.DECLARED,
                                       unit="s", source=src, inputs=inputs, exposure=expo, exposure_sources=srcs, link_ids=setting.link_ids)
                return self.fb.add("python.deadline", subject, None, FactStatus.UNRESOLVED, source=src, inputs=inputs, exposure=expo,
                                   exposure_sources=srcs, link_ids=setting.link_ids, unresolved_reason="the settings value it uses is unresolved")
        return self.fb.add("python.deadline", subject, None, FactStatus.UNRESOLVED, source=src,
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

    def collect(self) -> None:
        if _kw(self.call, "root_path") is not None:
            self.incomplete.append(f"{self.mod.path}:{self.call.lineno}: root_path")
        if _kw(self.call, "routes") is not None:
            self.incomplete.append(f"{self.mod.path}:{self.call.lineno}: routes=")
        self._object(self.mod, self.attr, "", [], 0)
        complete = not self.incomplete
        self.r.fb.add("fastapi.route_table", self.app_subject, {"complete": complete, "routes": self.count}, FactStatus.DECLARED,
                      source=ref(self.mod.path, self.call.lineno))
        if not complete:
            shown = "; ".join(self.incomplete[:5])
            self.r._cov(self.app_subject, f"The route table may be incomplete ({shown}); a path not listed may still be served.")

    def _object(self, mod: PyModule, name: str, prefix: str, refs: list[tuple[str, int]], depth: int) -> None:
        if depth > 10:
            self.incomplete.append(f"{mod.path}: routers nest too deeply")
            return
        for node in ast.walk(mod.tree):
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

_ = shlex  # kept for symmetry with spring.py command handling
