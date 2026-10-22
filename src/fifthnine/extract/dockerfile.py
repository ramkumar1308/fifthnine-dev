"""Dockerfile model (M2-1). Parses text only; nothing is built or executed.

Follows the Dockerfile reference for: parser directives (`# escape=`), line continuations, comment lines (also
inside continuations), instruction case, stages (`FROM ... AS name`), `ARG` scope (global ARGs before the first FROM
are visible only to FROM lines; a stage must redeclare them), `ENV` (both `k=v` and legacy `k v` forms), variable
substitution (`$V`, `${V}`, `${V:-w}`, `${V:+w}`) on the instructions that support it, `WORKDIR` relative to the
previous one, `EXPOSE`, `ENTRYPOINT`/`CMD` exec and shell forms with `SHELL`, and `COPY --from`.

Two distinctions are kept explicit (CEO clarification, M2-1):
- the *default target* is the final stage, which Docker builds when no `--target` is given;
- the *build target used for the deployed image* is not in the repository and is never assumed here.

Values that depend on something not in the file (an ARG without a default, a base image's WORKDIR, CMD or
ENTRYPOINT) are left unresolved with a reason rather than guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_INSTRUCTIONS = 10_000
MAX_STAGES = 200

_DIRECTIVE = re.compile(r"^#\s*([a-zA-Z][a-zA-Z0-9]*)\s*=\s*(.+?)\s*$")
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:[-+])([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")
# BuildKit recognises a heredoc only for a whole word like <<EOF, <<-EOF, 2<<"EOF"; a shell here-string (<<<) or an
# operator inside a longer word is ordinary shell text.
_HEREDOC_WORD = re.compile(r"^\d*<<-?([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1$")
SUBSTITUTED = frozenset({"ADD", "COPY", "ENV", "EXPOSE", "FROM", "LABEL", "STOPSIGNAL", "USER", "VOLUME", "WORKDIR", "ONBUILD", "ARG"})


class DockerfileError(ValueError):
    pass


@dataclass(frozen=True)
class Instruction:
    cmd: str  # upper case
    args: str  # continuation-joined, comment lines removed
    line: int  # first line (1-based)
    end_line: int


@dataclass
class Value:
    """A resolved value with its source line, or an unresolved one with a reason."""

    value: object
    line: int
    unresolved: str | None = None


@dataclass
class Stage:
    index: int
    name: str | None
    base: Value  # resolved base image reference, or "stage:<name>" when built FROM an earlier stage
    from_line: int
    parent: int | None = None  # index of the earlier stage this one is built from
    env: dict[str, Value] = field(default_factory=dict)
    args: dict[str, Value] = field(default_factory=dict)
    workdir: Value | None = None
    expose: list[Value] = field(default_factory=list)
    # Image config, as BuildKit computes it. A setting is "unknown" when it would come from an external base image.
    entrypoint: tuple[str, list[str], int] | None = None  # (form, argv, line)
    entrypoint_state: str = "unknown"  # "set" | "unknown"
    cmd: tuple[str, list[str], int] | None = None
    cmd_state: str = "unknown"  # "set" | "empty" (cleared by ENTRYPOINT) | "unknown"
    cmd_cleared_at: int | None = None  # ENTRYPOINT line that cleared an inherited CMD
    cmd_set_here: bool = False
    shell: list[str] = field(default_factory=lambda: ["/bin/sh", "-c"])
    copies: list[tuple[str | None, list[str], str, int]] = field(default_factory=list)  # (from, sources, dest, line)
    user: Value | None = None
    unsupported: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class DockerfileModel:
    path: str
    stages: list[Stage]
    global_args: dict[str, Value]
    unsupported: list[tuple[int, str]]

    @property
    def default_target(self) -> Stage | None:
        return self.stages[-1] if self.stages else None

    def chain(self, stage: Stage) -> list[Stage]:
        """The stage and the earlier stages it is built FROM, root first."""
        out = [stage]
        seen = {stage.index}
        while out[0].parent is not None and out[0].parent not in seen:
            seen.add(out[0].parent)
            out.insert(0, self.stages[out[0].parent])
        return out


def _logical_lines(text: str) -> tuple[list[Instruction], str, list[tuple[int, str]]]:
    lines = text.splitlines()
    escape = "\\"
    unsupported: list[tuple[int, str]] = []
    start = 0
    # parser directives: only at the very top, before any comment, blank line or instruction
    while start < len(lines):
        m = _DIRECTIVE.match(lines[start].strip())
        if not m or not lines[start].lstrip().startswith("#"):
            break
        if m.group(1).lower() == "escape":
            if m.group(2) not in ("\\", "`"):
                raise DockerfileError(f"invalid escape directive at line {start + 1}")
            escape = m.group(2)
        start += 1
    out: list[Instruction] = []
    i = start
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        first = i + 1
        parts: list[str] = []
        current = raw
        while True:
            body = current.rstrip()
            if body.endswith(escape) and not body.endswith(escape * 2):
                parts.append(body[:-1])
                i += 1
                while i < len(lines) and lines[i].strip().startswith("#"):  # comment lines inside a continuation are removed
                    i += 1
                if i >= len(lines):
                    break
                current = lines[i]
                continue
            parts.append(body)
            break
        joined = " ".join(p.strip() for p in parts if p.strip())
        word, _, rest = joined.partition(" ")
        cmd = word.upper()
        heredoc = None
        if cmd in ("RUN", "COPY", "ADD"):
            heredoc = next((m for w in rest.split() if (m := _HEREDOC_WORD.match(w))), None)
        if heredoc:
            delim = heredoc.group(2)
            j = i + 1
            while j < len(lines) and lines[j].strip() != delim:
                j += 1
            if j < len(lines):  # a heredoc with no terminator is not swallowed
                unsupported.append((first, f"{cmd} heredoc is not interpreted"))
                i = j
        out.append(Instruction(cmd, rest.strip(), first, i + 1))
        if len(out) > MAX_INSTRUCTIONS:
            raise DockerfileError(f"more than {MAX_INSTRUCTIONS} instructions")
        i += 1
    return out, escape, unsupported


def substitute(text: str, env: dict[str, Value], escape: str = "\\") -> tuple[str | None, str | None]:
    """Docker variable substitution. Returns (result, None) or (None, reason) when a referenced variable has no
    value in the file."""
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(4)
        op, word = m.group(2), m.group(3) or ""
        known = env.get(name)
        has = known is not None and known.unresolved is None
        val = str(known.value) if has and known is not None else ""
        if op == ":-":
            return val if has and val else word
        if op == ":+":
            return word if has and val else ""
        if not has:
            missing.append(name)
            return ""
        return val

    placeholder = "\x00ESC\x00"
    protected = text.replace(escape + "$", placeholder)
    result = _VAR.sub(repl, protected).replace(placeholder, "$")
    if missing:
        return None, "uses " + ", ".join(sorted(set(missing))) + ", which has no value in the Dockerfile (a build argument or base-image variable)"
    return result, None


def _split_words(text: str) -> list[str]:
    """Whitespace split honouring double and single quotes, as Docker does for ENV, ARG, LABEL."""
    words: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            else:
                cur.append(c)
        elif c in "\"'":
            quote = c
        elif c.isspace():
            if cur:
                words.append("".join(cur))
                cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        words.append("".join(cur))
    return words


def _exec_or_shell(args: str) -> tuple[str, list[str]]:
    s = args.strip()
    if s.startswith("["):
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return "exec", parsed
    return "shell", [s]


def _flags(args: str) -> tuple[dict[str, str], str]:
    flags: dict[str, str] = {}
    rest = args
    while rest.startswith("--"):
        token, _, rest = rest.partition(" ")
        k, _, v = token[2:].partition("=")
        flags[k] = v
        rest = rest.lstrip()
    return flags, rest


def parse_dockerfile(path: str, text: str) -> DockerfileModel:
    instructions, escape, unsupported = _logical_lines(text)
    global_args: dict[str, Value] = {}
    stages: list[Stage] = []
    names: dict[str, int] = {}
    for ins in instructions:
        if ins.cmd == "FROM":
            if len(stages) >= MAX_STAGES:
                raise DockerfileError(f"more than {MAX_STAGES} stages")
            flags, rest = _flags(ins.args)
            words = rest.split()
            if not words:
                raise DockerfileError(f"FROM without an image at line {ins.line}")
            name = words[2] if len(words) >= 3 and words[1].upper() == "AS" else None
            image_raw = words[0]
            resolved, reason = substitute(image_raw, global_args, escape)
            parent = names.get(resolved.lower()) if resolved else None
            base = Value(f"stage:{resolved}" if parent is not None else resolved, ins.line, reason)
            stage = Stage(index=len(stages), name=name, base=base, from_line=ins.line, parent=parent)
            if parent is not None:
                p = stages[parent]
                stage.env = dict(p.env)
                stage.workdir, stage.user, stage.shell = p.workdir, p.user, list(p.shell)
                stage.entrypoint, stage.entrypoint_state = p.entrypoint, p.entrypoint_state
                stage.cmd, stage.cmd_state, stage.cmd_cleared_at = p.cmd, p.cmd_state, p.cmd_cleared_at
                stage.expose = list(p.expose)
            if name:
                names[name.lower()] = stage.index
            stages.append(stage)
            continue
        if not stages:
            if ins.cmd == "ARG":
                for word in _split_words(ins.args):
                    k, eq, v = word.partition("=")
                    global_args[k] = Value(v, ins.line) if eq else Value(None, ins.line, f"build argument {k} has no default in the Dockerfile")
            else:
                unsupported.append((ins.line, f"{ins.cmd} before the first FROM"))
            continue
        st = stages[-1]
        scope = {**st.args, **st.env}
        if ins.cmd == "ARG":
            for word in _split_words(ins.args):
                k, eq, v = word.partition("=")
                if eq:
                    val, reason = substitute(v, scope, escape)
                    st.args[k] = Value(val, ins.line, reason)
                elif k in global_args:
                    st.args[k] = Value(global_args[k].value, ins.line, global_args[k].unresolved)
                else:
                    st.args[k] = Value(None, ins.line, f"build argument {k} has no default in the Dockerfile")
                scope = {**st.args, **st.env}
        elif ins.cmd == "ENV":
            words = _split_words(ins.args)
            pairs: list[tuple[str, str]] = []
            if words and "=" not in words[0]:
                k, _, v = ins.args.strip().partition(" ")  # legacy form: ENV key value with spaces
                pairs = [(k, " ".join(_split_words(v)))]
            else:
                pairs = [(w.partition("=")[0], w.partition("=")[2]) for w in words]
            for k, v in pairs:
                val, reason = substitute(v, scope, escape)
                st.env[k] = Value(val, ins.line, reason)
                scope = {**st.args, **st.env}
        elif ins.cmd == "WORKDIR":
            val, reason = substitute(ins.args.strip(), scope, escape)
            if reason is None and val is not None and not val.startswith("/"):
                prev = st.workdir
                if prev is None or prev.unresolved is not None:
                    st.workdir = Value(None, ins.line, "relative WORKDIR on top of the base image's WORKDIR, which is not in the repository")
                    continue
                val = str(prev.value).rstrip("/") + "/" + val
            st.workdir = Value(val, ins.line, reason)
        elif ins.cmd == "EXPOSE":
            for word in ins.args.split():
                val, reason = substitute(word, scope, escape)
                if reason or val is None:
                    st.expose.append(Value(None, ins.line, reason))
                    continue
                port, _, proto = val.partition("/")
                try:
                    st.expose.append(Value((int(port), (proto or "tcp").lower()), ins.line))
                except ValueError:
                    st.expose.append(Value(None, ins.line, f"EXPOSE value {val!r} is not a port"))
        elif ins.cmd in ("ENTRYPOINT", "CMD"):
            form, argv = _exec_or_shell(ins.args)
            if form == "shell":
                argv = [*st.shell, argv[0]]
            if ins.cmd == "ENTRYPOINT":
                st.entrypoint, st.entrypoint_state = (form, argv, ins.line), "set"
                if not st.cmd_set_here:
                    # Dockerfile reference: "If CMD is defined from the base image, setting ENTRYPOINT will reset CMD
                    # to an empty value." BuildKit clears it unless CMD was set in this stage.
                    st.cmd, st.cmd_state, st.cmd_cleared_at = None, "empty", ins.line
            else:
                st.cmd, st.cmd_state, st.cmd_set_here, st.cmd_cleared_at = (form, argv, ins.line), "set", True, None
        elif ins.cmd == "SHELL":
            form, argv = _exec_or_shell(ins.args)
            if form == "exec" and argv:
                st.shell = argv
            else:
                st.unsupported.append((ins.line, "SHELL must use the JSON form"))
        elif ins.cmd in ("COPY", "ADD"):
            flags, rest = _flags(ins.args)
            form, argv = _exec_or_shell(rest)
            parts = argv if form == "exec" else _split_words(rest)
            if len(parts) >= 2:
                src_dst: list[str] = []
                for part in parts:
                    val, reason = substitute(part, scope, escape)
                    src_dst.append(val if val is not None else part)
                st.copies.append((flags.get("from"), src_dst[:-1], src_dst[-1], ins.line))
        elif ins.cmd == "USER":
            val, reason = substitute(ins.args.strip(), scope, escape)
            st.user = Value(val, ins.line, reason)
        elif ins.cmd == "ONBUILD":
            st.unsupported.append((ins.line, "ONBUILD triggers are not interpreted"))
    return DockerfileModel(path=path, stages=stages, global_args=global_args, unsupported=unsupported)
