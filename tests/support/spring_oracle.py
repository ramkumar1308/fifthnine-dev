"""Semantic oracle for M2-2 Spring facts.

The oracle recomputes each Spring fact from the repository with code that shares nothing with the resolver:
- YAML configuration is composed with PyYAML (a YAML 1.1 implementation like SnakeYAML) and scalars are typed by its
  SafeConstructor, not by the engine's regular expressions or ruamel composer;
- `.properties` files are read with `javaproperties` (a port of java.util.Properties), not the engine's reader;
- layering, profile expansion, placeholder substitution, environment variable names and the actuator path algorithm are
  written again here from the Spring Boot documentation and source notes in docs/fifthnine/m0/RULES.md;
- defaults are read from the packaged table with PyYAML.

A fact passes only when the oracle's value and source line both agree. Facts the oracle does not model (Dockerfile ENV
winners, ISO-8601 durations, several containers for one module) are counted as skipped and reported.
"""

from __future__ import annotations

import io
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import javaproperties
import yaml

from fifthnine.schema.v1 import Fact, FactStatus, Linkage
from tests.support.oracle import OracleReport, RtObj, _bk_stages, _containers, load_rt

LOCS = ("src/main/resources", "src/main/resources/config")
EXTS = ("yaml", "yml", "properties")
SECRET = re.compile(r"(password|passwd|secret|token|credential|api-?key|private-?key|passphrase|access-?key)", re.I)


_EMPTY_IS_NULL = {"server.port", "management.server.port", "management.endpoints.web.exposure.include",
                  "management.endpoints.web.exposure.exclude", "management.endpoints.enabled-by-default"}


def ocanon(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "")


def oenv_legacy(key: str) -> str:
    return re.sub(r"\.+", "_", re.sub(r"\[(\d+)\]", r".\1.", key).replace("-", "_")).strip("_").upper()


def oenv(key: str) -> str:
    return re.sub(r"\.+", "_", re.sub(r"\[(\d+)\]", r".\1.", key).replace("-", "")).strip("_").upper()


# ---------------------------------------------------------------- reading files


@dataclass
class OEntry:
    key: str
    text: str  # value as Spring sees it, rendered as text
    line: int
    path: str
    layer: str
    profile: str | None  # None for base files
    indexed: bool = False


@dataclass
class ODoc:
    path: str
    entries: list[OEntry]
    on_profile: str | None = None
    other_condition: str | None = None  # e.g. "on-cloud-platform=kubernetes"


def _render(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _flatten(node: yaml.Node, prefix: str, loader: yaml.SafeLoader, out: list[tuple[str, Any, int]], line: int) -> None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            _flatten(v, f"{prefix}.{k.value}" if prefix else str(k.value), loader, out, k.start_mark.line + 1)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            _flatten(item, f"{prefix}[{i}]", loader, out, item.start_mark.line + 1)
    elif isinstance(node, yaml.ScalarNode) and prefix:
        out.append((prefix, loader.construct_object(node), line))


def read_config(root: Path, rel: str) -> list[ODoc]:
    text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
    docs: list[ODoc] = []
    if rel.endswith(".properties"):
        current: list[tuple[str, str, int]] = []
        line = 1
        groups: list[list[tuple[str, str, int]]] = [current]
        for ev in javaproperties.parse(io.StringIO(text)):
            if type(ev).__name__ == "Comment" and ev.source.strip() in ("#---", "!---"):
                current = []
                groups.append(current)
            elif type(ev).__name__ == "KeyValue":
                current.append((ev.key, ev.value, line))
            line += ev.source.count("\n")
        for g in groups:
            if g:
                last: dict[str, tuple[str, str, int]] = {}
                for k, v, ln in g:
                    last[k] = (k, v, ln)
                docs.append(_mk_doc(rel, list(last.values())))
        return docs
    loader = yaml.SafeLoader(text)
    try:
        while loader.check_node():
            node = loader.get_node()
            flat: list[tuple[str, Any, int]] = []
            _flatten(node, "", loader, flat, 1)
            docs.append(_mk_doc(rel, flat))
    finally:
        loader.dispose()
    return docs


def _mk_doc(rel: str, flat: list[tuple[str, Any, int]]) -> ODoc:
    entries: list[OEntry] = []
    on_profile: list[str] = []
    other: list[str] = []
    for k, v, ln in flat:
        if re.sub(r"\[\d+\]$", "", k).lower().replace("-", "") == "spring.config.activate.onprofile":
            on_profile.append(_render(v))
            continue
        if k.lower().startswith("spring.config.activate."):
            other.append(f"{k[len('spring.config.activate.'):].lower().replace('_', '-')}={_render(v)}")
            continue
        if k.lower().startswith("spring.config.import"):
            continue
        entries.append(OEntry(k, _render(v), ln, rel, "file", None))
    legacy = any(re.sub(r"\[\d+\]$", "", e.key) == "spring.profiles" for e in entries)  # Spring Boot 3 rejects the document
    return ODoc(rel, [] if legacy else entries, ",".join(on_profile) or None, ",".join(other) or None)


# ---------------------------------------------------------------- profile expressions


def _profile_match(expr: str, active: set[str]) -> bool:
    def one(item: str) -> bool:
        item = item.strip()
        if item.startswith("(") and item.endswith(")"):
            return _profile_match(item[1:-1], active)
        for op, fn in (("|", any), ("&", all)):
            parts = _split_top(item, op)
            if len(parts) > 1:
                return fn(one(p) for p in parts)
        return item[1:].strip() not in active if item.startswith("!") else item in active
    return any(one(p) for p in _split_top(expr, ","))


def _split_top(s: str, sep: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        depth += ch == "("
        depth -= ch == ")"
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [*out, cur]


# ---------------------------------------------------------------- the module view


@dataclass
class Ctx:
    root: Path
    module: str  # "." or a directory
    env: dict[str, tuple[str | None, str, int]] = field(default_factory=dict)  # var -> (value or None if unresolved, path, line)
    unknown_secret: bool = False
    args: list[tuple[str, str, int]] = field(default_factory=list)  # (token, path, line)
    k8s_cmd: list[tuple[str, str, int]] | None = None  # container command, None when unset
    k8s_args: list[tuple[str, str, int]] | None = None
    docker_ep: list[tuple[str, str, int]] | None = None  # image ENTRYPOINT of the default stage chain
    docker_cmd: list[tuple[str, str, int]] | None = None
    docker_env: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # var -> (value, Dockerfile, line) of the default stage
    linked: bool = False
    layers: list[list[OEntry]] = field(default_factory=list)  # low to high, files only
    cache: dict[str, list[ODoc]] = field(default_factory=dict)

    def res_dirs(self) -> list[str]:
        base = "" if self.module == "." else self.module + "/"
        return [base + d for d in LOCS]

    def docs(self, stem: str) -> list[ODoc]:
        out: list[ODoc] = []
        for d in self.res_dirs():
            for ext in EXTS:
                rel = f"{d}/{stem}.{ext}"
                if (self.root / rel).is_file():
                    if rel not in self.cache:
                        try:
                            self.cache[rel] = read_config(self.root, rel)
                        except Exception:  # noqa: BLE001 - unreadable files are reported by the engine
                            self.cache[rel] = []
                    out.extend(self.cache[rel])
        return out


def _container_env(root: Path, container_ref: str, ctx: Ctx) -> None:
    objs = load_rt(root, sorted(p.relative_to(root).as_posix() for p in list(root.rglob("*.yml")) + list(root.rglob("*.yaml"))
                                if ".git" not in p.parts and "target" not in p.parts))
    # container_ref is k8s:Deployment/ns/name/container
    body = container_ref[len("k8s:"):]
    kind, ns, name, cname = body.split("/", 3)
    dep = next((o for o in objs if o.kind == kind and o.ns == ns and o.name == name), None)
    if dep is None:
        return
    c = _containers(dep).get(cname)
    if c is None:
        return
    cms: dict[str, RtObj] = {o.name: o for o in objs if o.kind == "ConfigMap" and o.ns == ns}
    for src in c.get("envFrom") or []:
        if "secretRef" in src:
            ctx.unknown_secret = True
        elif "configMapRef" in src:
            cm = cms.get(str(src["configMapRef"].get("name")))
            if cm is None:
                ctx.unknown_secret = True
                continue
            data = cm.doc.get("data") or {}
            for k in data:
                ctx.env[f"{src.get('prefix') or ''}{k}"] = (str(data[k]), cm.path, data.lc.key(k)[0] + 1)
    for item in c.get("env") or []:
        n = str(item["name"])
        if "valueFrom" in item:
            ctx.env[n] = (None, dep.path, item.lc.key("valueFrom")[0] + 1)
        else:
            v = item.get("value")
            ctx.env[n] = ("" if v is None else str(v), dep.path, item.lc.key("value")[0] + 1 if "value" in item else item.lc.key("name")[0] + 1)
    for field_name in ("command", "args"):
        seq = c.get(field_name) or []
        toks = [(str(tok), dep.path, seq.lc.item(i)[0] + 1) for i, tok in enumerate(seq)]
        ctx.args += toks
        if toks:  # an empty list counts as unset
            if field_name == "command":
                ctx.k8s_cmd = toks
            else:
                ctx.k8s_args = toks


def _docker_env(root: Path, link: Linkage, ctx: Ctx) -> None:
    paths = sorted({e.rsplit(":", 1)[0] for e in link.evidence if re.fullmatch(r"\S+:\d+", e) and "Dockerfile" in e.rsplit(":", 1)[0]})
    if len(paths) != 1 or not (root / paths[0]).is_file():
        return
    stages = _bk_stages((root / paths[0]).read_text(encoding="utf-8-sig", errors="replace"))
    if not stages:
        return
    chain = [stages[-1]]
    while True:
        parent = next((st for st in stages[:chain[0].index] if st.name and st.name == chain[0].base), None)
        if parent is None:
            break
        chain.insert(0, parent)
    ep: list[tuple[str, str, int]] | None = None
    cm: list[tuple[str, str, int]] | None = None
    for st in chain:
        cmd_here = False
        for cmd in st.cmds:
            kind = cmd.cmd.upper()
            if kind in ("ENTRYPOINT", "CMD"):
                argv = list(cmd.value) if cmd.json else [" ".join(cmd.value)]
                toks = [(a, paths[0], cmd.start_line) for a in argv]
                if kind == "ENTRYPOINT":
                    ep = toks
                    if not cmd_here:
                        cm = None
                else:
                    cm, cmd_here = toks, True
                continue
            if kind != "ENV":
                continue
            vals = list(cmd.value)
            for k, v in zip(vals[0::2], vals[1::2], strict=False):  # BuildKit flattens ENV into name, value, name, value
                v = v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v
                if "$" not in v:
                    ctx.docker_env[k] = (v, paths[0], cmd.start_line)
    ctx.docker_ep, ctx.docker_cmd = ep, cm


# ---------------------------------------------------------------- layered lookup


@dataclass
class Win:
    text: str | None  # None when unresolvable
    path: str | None
    line: int | None
    layer: str
    why: str = ""


def _words(token: str) -> list[str]:
    try:
        return shlex.split(token) if re.search(r"\s", token) else [token]
    except ValueError:
        return []


def _flat_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    out: dict[str, str] = {}

    def walk(p: str, n: Any) -> None:
        if isinstance(n, (dict, list)) and not n and p:
            out[p] = ""
        elif isinstance(n, dict):
            for k, v in n.items():
                walk(f"{p}.{k}" if p else k, v)
        elif isinstance(n, list):
            for i, v in enumerate(n):
                walk(f"{p}[{i}]", v)
        elif p and n is not None:
            out[p] = _render(n)

    if isinstance(data, dict):
        walk("", data)
    return out


UNRESOLVED = "\0unresolved"


class _Unresolved(Exception):
    """A referenced environment variable comes from a source outside the repository."""


class View:
    def __init__(self, ctx: Ctx, profiles: list[str] | None) -> None:
        self.ctx = ctx
        self.profiles = profiles
        self.uncertain: set[str] = set()
        self.files = self._file_layers(profiles)
        self.sys: dict[str, tuple[str, str, int]] = {}
        self.json: dict[str, tuple[str, str, int]] = {}
        self.cli: dict[str, tuple[str, str, int]] = {}
        self._overrides()
        self._fold_lists()
        self.redirected = self._redirected()
        if self.redirected:
            self.files = []

    def _file_layers(self, profiles: list[str] | None) -> list[OEntry]:
        out: list[OEntry] = []
        active = set(profiles) if profiles else {"default"}
        for doc in self.ctx.docs("application"):
            if doc.on_profile is not None and (profiles is None or not _profile_match(doc.on_profile, active)):
                continue
            if doc.other_condition is not None:
                if profiles is None:
                    continue
                on = doc.other_condition == "on-cloud-platform=kubernetes" and self.ctx.linked
                off = doc.other_condition.startswith("on-cloud-platform=") and self.ctx.linked and not on
                if off:
                    continue
                if not on:
                    for e in doc.entries:
                        self.uncertain.add(ocanon(re.sub(r"\[\d+\]$", "", e.key)))
                    continue
            out += [OEntry(e.key, e.text, e.line, e.path, "file", doc.on_profile) for e in doc.entries]
        for p in profiles or ["default"]:
            for doc in self.ctx.docs(f"application-{p}"):
                out += [OEntry(e.key, e.text, e.line, e.path, "file", p) for e in doc.entries]
        return out

    def _overrides(self) -> None:
        for var in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS"):
            v = self.ctx.env.get(var)
            if v and v[0]:
                for w in _words(v[0]):
                    if w.startswith("-D") and "=" in w:
                        k, _, val = w[2:].partition("=")
                        self.sys[ocanon(k)] = (val, v[1], v[2])
        ctx = self.ctx
        if ctx.k8s_cmd is not None:
            stream = list(ctx.k8s_cmd) + list(ctx.k8s_args or [])
        elif ctx.docker_ep is not None and ctx.linked:
            stream = list(ctx.docker_ep) + (list(ctx.k8s_args) if ctx.k8s_args is not None else list(ctx.docker_cmd or []))
        else:
            stream = list(ctx.args)
        program_args = False
        skip = False
        seen_java = False
        ended = False
        shell = any(w.rsplit("/", 1)[-1] in ("sh", "bash", "ash", "dash", "zsh") for tok, _p, _l in stream for w in _words(tok))
        for tok, path, line in stream:
            for w in _words(tok):
                if skip:
                    skip = False
                elif not program_args and w.rsplit("/", 1)[-1] in ("java", "java.exe"):
                    seen_java = True
                elif not program_args and seen_java and re.fullmatch(r"[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)+", w):
                    program_args = True
                elif not program_args and w == "-jar":
                    program_args = skip = True
                elif not program_args and w in ("-cp", "-classpath", "--class-path", "-p", "--module-path"):
                    skip = True
                elif not program_args and w.startswith("-D") and "=" in w:
                    k, _, val = w[2:].partition("=")
                    self.sys[ocanon(k)] = (val, path, line)
                elif program_args and w == "--":
                    ended = True
                elif shell and re.search(r"\$[{A-Za-z_]", w):
                    continue  # expanded by the shell at startup
                elif not ended and w.startswith("--") and len(w) > 2:
                    k, _, val = w[2:].partition("=")
                    prev = self.cli.get(ocanon(k))
                    # a repeated option joins its values with commas (indexed keys are separate entries)
                    self.cli[ocanon(k)] = (f"{prev[0]},{val}", prev[1], prev[2]) if prev and not re.search(r"\[\d+\]$", k) else (val, path, line)
        # spring.application.json: command line, then system property, then SPRING_APPLICATION_JSON
        jc = ocanon("spring.application.json")
        env_json = self.ctx.env.get("SPRING_APPLICATION_JSON")
        src = self.cli.get(jc) or self.sys.get(jc) or ((env_json[0], env_json[1], env_json[2]) if env_json and env_json[0] else None)
        if src is not None:
            for k, val in _flat_json(src[0]).items():
                self.json[ocanon(k)] = (val, src[1], src[2])

    def _fold_lists(self) -> None:
        """`k[0]=a k[1]=b` in one property source (JSON, system properties or command line) is the list `k`: join it as a comma list."""
        for table in (self.sys, self.json, self.cli):
            groups: dict[str, list[tuple[int, tuple[str, str, int]]]] = {}
            for key in [k for k in table if re.search(r"\[\d+\]$", k)]:
                base = re.sub(r"\[\d+\]$", "", key)
                groups.setdefault(base, []).append((int(re.search(r"\[(\d+)\]$", key).group(1)), table.pop(key)))  # type: ignore[union-attr]
            for base, items in groups.items():
                items.sort(key=lambda t: t[0])
                table[base] = (",".join(v[0] for _, v in items), items[0][1][1], items[0][1][2])

    def _redirected(self) -> bool:
        """spring.config.location / spring.config.name, set outside the config files, make Boot read other files than application.*"""
        for key in ("spring.config.location", "spring.config.name"):
            c = ocanon(key)
            top: str | None = None
            found = False
            for table in (self.cli, self.json, self.sys):
                if c in table:
                    top, found = table[c][0], True
                    break
            if not found:
                for name in dict.fromkeys([oenv(key), oenv_legacy(key)]):
                    if name in self.ctx.env:
                        top, found = self.ctx.env[name][0], True
                        break
                    if name in self.ctx.docker_env:
                        top, found = self.ctx.docker_env[name][0], True
                        break
            if not found:
                continue
            if top is None or "${" in top:
                return True
            if key.endswith("name"):
                if top.strip() != "application":
                    return True
            elif re.sub(r"\s+|optional:", "", top) != "classpath:/;classpath:/config/":
                return True
        return False

    def entries(self, key: str, raw: bool = False) -> list[tuple[str, str | None, int | None, str, str | None]]:
        """(text, path, line, layer, why) for every source of `key`, highest first. Lists given as `k[0]` replace each other
        as a whole, so they are folded into one entry per file layer."""
        c = ocanon(key)
        out: list[tuple[str, str | None, int | None, str, str | None]] = []
        for layer, table in (("args", self.cli), ("json", self.json), ("sysprop", self.sys)):
            if c in table:
                out.append((table[c][0], table[c][1], table[c][2], layer, None))
        for name in dict.fromkeys([oenv(key), oenv_legacy(key)]):
            if name in self.ctx.env:
                val, path, line = self.ctx.env[name]
                out.append((val if val is not None else UNRESOLVED, path, line, "env", None))
            if name in self.ctx.docker_env:
                val, path, line = self.ctx.docker_env[name]
                out.append((val, path, line, "docker_env", None))
            idx = [i for i in range(64) if f"{name}_{i}_" in self.ctx.env]
            if idx:  # NAME_0_, NAME_1_ ... form one list in the environment source
                parts = [self.ctx.env[f"{name}_{i}_"] for i in idx]
                out.append((",".join("" if p[0] is None else p[0] for p in parts), parts[0][1], parts[0][2], "env", None))
        grouped: dict[tuple[str, str | None], list[OEntry]] = {}
        order: list[tuple[str, str | None]] = []
        for e in self.files:
            base = re.sub(r"\[\d+\]$", "", e.key)
            if ocanon(base) != c:
                continue
            gk = (e.path, e.profile if e.profile is not None else "")
            if gk not in grouped:
                grouped[gk] = []
                order.append(gk)
            grouped[gk].append(e)
        for gk in reversed(order):
            es = grouped[gk]
            if es[0].key != re.sub(r"\[\d+\]$", "", es[0].key):  # indexed
                es.sort(key=lambda x: int(re.search(r"\[(\d+)\]$", x.key).group(1)))  # type: ignore[union-attr]
                out.append((",".join(x.text for x in es), es[0].path, es[0].line, "file", es[0].profile))
            else:
                out.append((es[-1].text, es[-1].path, es[-1].line, "file", es[-1].profile))
        if key in _EMPTY_IS_NULL or (key.startswith("management.endpoint") and key.endswith((".enabled", ".enabled-by-default"))):
            if raw:
                return out
            if out and out[0][0] == "":
                return []  # an empty top value is null: it hides the lower layers and the default applies
            out = [e for e in out if e[0] != ""]  # Boot converts "" to null for Integer, Boolean and an include/exclude list
        return out

    def lookup(self, name: str, depth: int = 0) -> str | None:
        """Placeholder value of `name` (exact environment variable first, then any property source)."""
        if name in self.ctx.env:
            if self.ctx.env[name][0] is None:
                raise _Unresolved(name)
            return self.ctx.env[name][0]
        if name in self.ctx.docker_env:
            return self.ctx.docker_env[name][0]
        es = self.entries(name)
        if name != name.lower() or "_" in name:  # an invalid canonical name is looked up by its exact spelling in files
            es = [e for e in es if e[3] != "file" or any(f.key == name and f.path == e[1] for f in self.files)]
        if not es:
            if name.startswith(("random.", "java.", "os.", "user.", "sun.", "jdk.", "native.")) or name in (
                    "HOSTNAME", "KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT", "HOME", "PATH", "USER", "PWD", "LANG", "TMPDIR", "JAVA_HOME",
                    "line.separator", "file.separator", "path.separator"):
                raise _Unresolved(name)  # supplied by the JVM or the container runtime
            return None
        if es[0][0] == UNRESOLVED:
            raise _Unresolved(name)
        return self.expand(es[0][0], depth + 1)

    def expand(self, text: str, depth: int = 0) -> str | None:
        if "${" not in text:
            return text
        if depth > 10:
            return None
        out = ""
        i = 0
        while i < len(text):
            if text.startswith("${", i):
                level, j = 1, i + 2
                while j < len(text) and level:
                    level += text.startswith("${", j)
                    level -= text[j] == "}"
                    j += 2 if text.startswith("${", j) else 1
                if level:
                    return None
                inner = text[i + 2:j - 1]
                cut, lvl = None, 0
                for n, ch in enumerate(inner):
                    lvl += inner.startswith("${", n)
                    lvl -= ch == "}"
                    if ch == ":" and lvl == 0:
                        cut = n
                        break
                name = inner if cut is None else inner[:cut]
                default = None if cut is None else inner[cut + 1:]
                name = self.expand(name, depth + 1) or ""
                val = self.lookup(name, depth)
                if val is None and default is not None:
                    val = self.expand(default, depth + 1)
                if val is None:
                    return None
                out += val
                i = j
            else:
                out += text[i]
                i += 1
        return out

    def winner(self, key: str) -> Win | None:
        es = self.entries(key)
        if ocanon(key) in self.uncertain and (not es or es[0][3] in ("file", "docker_env")):
            return Win(None, None, None, "uncertain", "document condition not evaluated")
        if not es:
            return Win(None, None, None, "files-unknown", "config location replaced") if self.redirected else None
        text, path, line, layer, _ = es[0]
        if text == UNRESOLVED:
            return Win(None, path, line, layer, "valueFrom")
        try:
            resolved = self.expand(text)
        except _Unresolved as exc:
            return Win(None, path, line, layer, f"valueFrom {exc}")
        if resolved is None:
            return Win(None, path, line, layer, "placeholder")
        return Win(resolved, path, line, layer)


# ---------------------------------------------------------------- profiles


def oracle_profiles(ctx: Ctx, option: list[str] | None) -> list[str] | None:
    """Active profiles, groups expanded; None when they cannot be determined from the repository."""
    if option is not None:
        names = option
    else:
        base = View(ctx, None)
        es = base.entries("spring.profiles.active")
        if not es:
            if base.redirected:
                return None  # the profile may be set in files the repository does not show
            names = []
        else:
            text = base.expand(es[0][0]) if es[0][0] != UNRESOLVED else None
            if text is None:
                return None
            names = [p.strip() for p in text.split(",") if p.strip()]
        if any(d.other_condition is not None and any(e.key.lower().startswith("spring.profiles.") for e in d.entries) for d in ctx.docs("application")):
            return None  # a conditional document that sets profiles: decided at startup
        inc = [e[0] for e in base.entries("spring.profiles.include")]
        inc += [e.text for e in base.files if re.sub(r"\[\d+\]$", "", e.key) == "spring.profiles.include" and e.text not in inc]
        union: list[str] = []
        for raw in inc:  # includes from every source and document are a union
            text = base.expand(raw)
            union += [p.strip() for p in (text or "").split(",") if p.strip() and p.strip() not in union]
        names = union + names
        def groups_of(n: str) -> list[str]:
            es = base.entries(f"spring.profiles.group.{n}")
            if not es:
                return []
            text = base.expand(es[0][0]) if es[0][0] != UNRESOLVED else None
            if text is None:
                raise _Unresolved(n)
            return [p.strip() for p in text.split(",") if p.strip()]

        try:
            names = _expand_groups(names, groups_of)
        except _Unresolved:
            return None
        if not names:  # Boot accepts the default profiles; spring.profiles.default is read from the unconditional sources only
            d = base.entries("spring.profiles.default")
            if d:
                text = base.expand(d[0][0]) if d[0][0] != UNRESOLVED else None
                if text is None:
                    return None
                names = _expand_groups([p.strip() for p in text.split(",") if p.strip()], groups_of)
        return names
    return _expand_groups(names, {})


def _expand_groups(names: list[str], groups: Any) -> list[str]:
    get = groups if callable(groups) else (lambda n: groups.get(n, []))
    out: list[str] = []

    def visit(n: str) -> None:
        if n not in out:
            out.append(n)
        for child in get(n):
            visit(child)

    for n in names:
        visit(n)
    return out


# ---------------------------------------------------------------- comparing

_DUR = re.compile(r"^(\d+)(ns|us|ms|s|m|h|d)?$", re.I)


def fact_text(f: Fact) -> str:
    v = f.value
    if v is None:
        return ""
    if isinstance(v, bool):
        text = "true" if v else "false"
    elif isinstance(v, list):
        text = ",".join(_render(x) for x in v)
    else:
        text = str(v)
    return f"{text}{f.unit}" if f.unit and not isinstance(v, (list, dict)) else text


def same_value(f: Fact, oracle_text: str) -> bool:
    if f.value == "<redacted>":
        return bool(SECRET.search(f.subject.key.rsplit(".", 1)[-1])) or "@" in oracle_text or "password=" in oracle_text.lower()
    got = fact_text(f)
    if got == oracle_text:
        return True
    if isinstance(f.value, bool):  # Spring's StringToBooleanConverter
        word = oracle_text.strip().lower()
        if word in ("true", "on", "yes", "1", "false", "off", "no", "0"):
            return (word in ("true", "on", "yes", "1")) is f.value
    if "<redacted>" in got:
        pattern = re.escape(got).replace(re.escape("<redacted>"), ".+?")
        return re.fullmatch(pattern, oracle_text) is not None
    if f.unit:
        m = _DUR.match(oracle_text.strip())
        if m and not m.group(2):  # a bare number is in the property's default unit, milliseconds for durations
            return f"{m.group(1)}ms" == got
    if isinstance(f.value, list):
        return [x.strip() for x in oracle_text.split(",") if x.strip()] == [_render(x) for x in f.value]
    return False


# ---------------------------------------------------------------- the check


def _defaults() -> dict[str, Any]:
    p = Path(__file__).resolve().parents[2] / "src/fifthnine/data/defaults/spring-boot.v1.yaml"
    return yaml.safe_load(p.read_text())  # type: ignore[no-any-return]


def check_spring(root: Path, facts: list[Fact], links: list[Linkage], rep: OracleReport, *, option: list[str] | None = None) -> None:
    by_link = {lk.link_id: lk for lk in links}
    table = _defaults()
    modules: dict[str, list[Fact]] = {}
    for f in facts:
        if f.kind.startswith("spring.") or (f.kind == "stack.version" and f.subject.key.startswith("component:spring-boot")):
            sub = f.subject.key
            if f.kind == "stack.version":
                continue
            mod = sub[len("spring:"):].split(":", 1)[0]
            modules.setdefault(mod, []).append(f)
    boot = {f.subject.key: f for f in facts if f.kind == "stack.version" and f.subject.key.startswith("component:spring-boot")}
    for mod, mfacts in sorted(modules.items()):
        if "@" in mod:
            rep.skip("spring: several containers for one module")
            continue
        ctx = Ctx(root, mod)
        active_fact = next((f for f in mfacts if f.kind == "spring.active_profiles"), None)
        if active_fact is not None and active_fact.link_ids:
            link = by_link.get(active_fact.link_ids[0])
            if link is not None:
                ctx.linked = True
                _container_env(root, link.from_ref, ctx)
                _docker_env(root, link, ctx)
        version_fact = boot.get("component:spring-boot" if mod == "." else f"component:spring-boot@{mod}")
        version = str(version_fact.value) if version_fact is not None and version_fact.value else None
        minor = ".".join(version.split(".")[:2]) if version else None
        profiles = oracle_profiles(ctx, option)
        if active_fact is not None:
            rep.checked += 1
            if profiles is None:
                if active_fact.status is not FactStatus.UNRESOLVED:
                    rep.mismatches.append(f"spring.active_profiles {mod}: oracle cannot resolve, engine says {active_fact.status.value}")
            elif active_fact.value != profiles:
                rep.mismatches.append(f"spring.active_profiles {mod}: value {active_fact.value!r}, oracle {profiles!r}")
        if profiles is None:
            rep.skip("spring: profiles unresolved")
            continue
        view = View(ctx, profiles)
        label = ",".join(profiles) or "default"
        for f in mfacts:
            if f.kind == "spring.property":
                _check_property(f, mod, view, label, table, minor, rep)
            elif f.kind == "spring.actuator.endpoint":
                _check_endpoint(f, mod, view, table, minor, mfacts, rep)


def _check_property(f: Fact, mod: str, view: View, label: str, table: dict[str, Any], minor: str | None, rep: OracleReport) -> None:
    key = f.subject.key[len(f"spring:{mod}:"):]
    if f.status is FactStatus.OVERRIDDEN:
        rep.checked += 1
        es = view.entries(key, raw=True)
        hit = [e for e in es[1:] if f.source is not None and e[1] == f.source.path and e[2] == f.source.line_start]
        if not hit and f.source is not None:  # a lower document of the same file is a separate property source
            hit = [e for e in view.files if re.sub(r"\[\d+\]$", "", e.key) == key and e.path == f.source.path and e.line == f.source.line_start]
        if not hit:
            rep.mismatches.append(f"spring.property {key} (overridden): no lower-precedence source at {f.source}")
        return
    if f.scope.get("profile") != label:
        rep.mismatches.append(f"spring.property {key}: scope {f.scope}, oracle active profiles {label}")
        return
    win = view.winner(key)
    if f.status is FactStatus.DEFAULT:
        rep.checked += 1
        if win is not None and win.layer == "files-unknown":
            rep.mismatches.append(f"spring.property {key}: reported as a default, but the oracle cannot read the configuration files ({win.why})")
            return
        if win is not None:
            rep.mismatches.append(f"spring.property {key}: reported as a default, but the oracle finds {win.layer} {win.path}:{win.line}")
            return
        entry = table["entries"].get(key) or table["entries"].get(re.sub(r"\.[^.]+$", ".*", key))
        row = entry["versions"].get(minor) if entry and minor else None
        if row is None:
            rep.mismatches.append(f"spring.property {key}: no default in the table for Boot {minor}")
        elif row["value"] != f.value and not (f.value in ("", None) and row["value"] in ("", None)):
            rep.mismatches.append(f"spring.property {key}: default {f.value!r}, table {row['value']!r}")
        return
    if win is None:
        rep.mismatches.append(f"spring.property {key}: oracle finds no source, engine reports {f.status.value}")
        return
    if f.source is not None and f.source.path.endswith("Dockerfile"):
        rep.skip("spring: Dockerfile ENV winner")
        return
    rep.checked += 1
    if f.status is FactStatus.UNRESOLVED:
        if win.text is not None:
            rep.mismatches.append(f"spring.property {key}: reported unresolved, oracle resolves {win.text!r}")
        return
    if win.text is None:
        rep.mismatches.append(f"spring.property {key}: oracle cannot resolve ({win.why}), engine says {f.value!r}")
        return
    if "PT" in win.text.upper() and f.unit:
        rep.skip("spring: ISO-8601 duration")
        return
    if not same_value(f, win.text):
        rep.mismatches.append(f"spring.property {key}: value {f.value!r} {f.unit or ''}, oracle {win.text!r}")
        return
    got = (f.source.path, f.source.line_start) if f.source else None
    # A value that is exactly one placeholder cites where the referenced value is defined (a container variable or another
    # property); anything else cites the line of the key itself.
    expected = (win.path, win.line)
    es = view.entries(key)
    m = re.fullmatch(r"\$\{([^:{}]+)(?::.*)?\}", es[0][0], re.S) if es else None
    if m is not None:
        name = m.group(1)
        if name in view.ctx.env:
            expected = view.ctx.env[name][1:]  # type: ignore[assignment]
        elif name in view.ctx.docker_env:
            expected = view.ctx.docker_env[name][1:]  # type: ignore[assignment]
        else:
            ref = view.entries(name)
            if name != name.lower() or "_" in name:  # same exact-spelling rule as the lookup
                ref = [e for e in ref if e[3] != "file" or any(x.key == name and x.path == e[1] for x in view.files)]
            if ref and ref[0][1] is not None:
                expected = (ref[0][1], ref[0][2])
    if got != expected:
        rep.mismatches.append(f"spring.property {key}: source {got}, oracle {expected[0]}:{expected[1]}")


def _check_endpoint(f: Fact, mod: str, view: View, table: dict[str, Any], minor: str | None, mfacts: list[Fact], rep: OracleReport) -> None:
    if f.status is FactStatus.UNRESOLVED or f.absent:
        rep.skip("spring: unresolved or absent endpoint")
        return

    def val(key: str, default: Any) -> Any:
        w = view.winner(key)
        return default if w is None or w.text is None else w.text

    group = f.subject.key.rsplit("/", 1)[1]
    mgmt_raw = view.winner("management.server.port")
    server_raw = view.winner("server.port")
    mgmt = int(mgmt_raw.text) if mgmt_raw and mgmt_raw.text and mgmt_raw.text.lstrip("-").isdigit() else None
    server = int(server_raw.text) if server_raw and server_raw.text and server_raw.text.isdigit() else None
    if mgmt is None:
        kind = "SAME"
    elif mgmt < 0:
        kind = "DISABLED"
    elif (server is None and mgmt == 8080) or (mgmt != 0 and mgmt == server):
        kind = "SAME"
    else:
        kind = "DIFFERENT"

    def clean(p: str) -> str:
        p = p.strip()
        return "" if p in ("", "/") else "/" + p.strip("/")

    base = clean(val("management.endpoints.web.base-path", "/actuator"))
    seg = str(val("management.endpoints.web.path-mapping.health", "health")).strip("/") or "health"
    root_path = clean(val("management.server.base-path", "")) if kind == "DIFFERENT" else clean(val("server.servlet.context-path", ""))
    port = mgmt if kind == "DIFFERENT" else (server if server is not None else 8080)
    expect = {"port": port, "path": f"{root_path}{base}/{seg}/{group}", "port_type": kind}
    rep.checked += 1
    got = {k: f.value.get(k) for k in ("port", "path", "port_type")} if isinstance(f.value, dict) else f.value
    if got != expect:
        rep.mismatches.append(f"spring.actuator.endpoint {f.subject.key}: value {got!r}, oracle {expect!r}")


__all__ = ["check_spring"]
