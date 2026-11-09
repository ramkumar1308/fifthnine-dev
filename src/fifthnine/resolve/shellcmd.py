"""A small, deliberately incomplete model of `sh -c "<script>"` as a container runs it.

Only one simple command is modelled: optional leading `exec`, then words built from literal text, single and double
quotes, backslash escapes, `$NAME`, `${NAME}`, `${NAME:-word}`, `${NAME-word}`, the positional parameters (`$0` to `$9`,
`$@`, `$*`, `"$@"`, `${@}`, `${*}`, `$#`) and redirections, which do not change the arguments. Anything else (several
commands, pipes, substitutions, arithmetic, assigning expansions such as `${NAME:=word}`, other parameter forms, leading
assignments, and brace expansion in bash and ksh) makes the script opaque, and the caller must treat every option it
could set as unknown. Nothing is executed.

Expansion follows POSIX field splitting with the default IFS (space, tab, newline): an unquoted expansion splits on those
characters and an empty unquoted expansion produces no word; a quoted expansion is one word. A variable whose value is
unknown is recorded on the word it appears in, with whether it was quoted, so the caller can decide what that word could be.
zsh is not a modelled shell: it does not split unquoted expansions by default.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

SHELLS = frozenset({"sh", "bash", "ash", "dash", "ksh"})  # POSIX field splitting
OTHER_SHELLS = frozenset({"zsh", "fish", "csh", "tcsh"})  # shells whose expansion rules are not modelled
BRACE_SHELLS = frozenset({"bash", "ksh"})
_OPERATORS = set(";&|()`")
_NAME_START = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_NAME_CHARS = _NAME_START | set("0123456789")
_IFS = re.compile(r"[ \t\n]+")


@dataclass
class ShellWord:
    text: str  # expanded text; an unknown expansion contributes nothing to it
    origin: int = -1  # -1: the script; k >= 0: positional argument k ($0 is 0)
    unknown: list[tuple[str, bool]] = field(default_factory=list)  # (variable, quoted) whose value is unknown
    unknown_before_eq: bool = False  # an unknown expansion occurs before the first "=" of the word


@dataclass
class ShellParse:
    words: list[ShellWord]
    opaque: str | None = None  # why the script cannot be modelled
    referenced: list[str] = field(default_factory=list)  # environment variables the script expands


@dataclass
class Invocation:
    shell: str
    script: str | None  # None: the shell runs a script file or reads standard input
    script_index: int  # index of the script argument in argv
    opaque: str | None = None


# lookup(name) -> (value, known). known=False: the value cannot be known. known=True with value None: the variable is unset.
Lookup = Callable[[str], "tuple[str | None, bool]"]


class _Opaque(Exception):
    pass


def invocation(argv: list[str], at: int) -> Invocation:
    """Read the options of the shell at argv[at] (POSIX sh, dash, bash, ksh): `-c` alone or combined (`-ec`), set options such
    as `-e`, `-x`, `-o pipefail`, `+o name`, bash's `--norc`, `--noprofile`, `--posix`, and `--` before the script. Login and
    interactive shells source profile files, so they are opaque."""
    shell = argv[at].rsplit("/", 1)[-1]
    i = at + 1
    has_c = False
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1
            break
        if a in ("--norc", "--noprofile", "--posix", "--noediting"):
            i += 1
            continue
        if a.startswith("--"):
            return Invocation(shell, None, -1, opaque=f"the shell option {a} is not modelled")
        if re.fullmatch(r"[-+]o", a):
            i += 2
            continue
        if re.fullmatch(r"[-+][a-zA-Z]+", a):
            letters = a[1:]
            if a[0] == "-" and any(ch in letters for ch in "lisO"):
                return Invocation(shell, None, -1, opaque=f"{shell} {a} reads profile files or standard input")
            has_c = has_c or (a[0] == "-" and "c" in letters)
            i += 1
            continue
        break
    if not has_c:
        return Invocation(shell, None, -1)
    if i >= len(argv):
        return Invocation(shell, None, -1, opaque="the shell is given -c but no command")
    return Invocation(shell, argv[i], i)


def expand(script: str, positional: list[str], lookup: Lookup, *, brace: bool = False) -> ShellParse:
    referenced: list[str] = []
    try:
        words = _Expander(script, positional, lookup, referenced, brace).run()
    except _Opaque as exc:
        return ShellParse([], opaque=str(exc), referenced=referenced)
    if words and words[0].text == "exec" and not words[0].unknown and words[0].origin == -1:
        words = words[1:]
    if words and not words[0].unknown and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0].text, re.S):
        return ShellParse([], opaque="the script assigns variables before the command", referenced=referenced)
    return ShellParse(words, referenced=referenced)


class _Expander:
    def __init__(self, s: str, positional: list[str], lookup: Lookup, referenced: list[str], brace: bool) -> None:
        self.s, self.i, self.pos, self.lookup, self.referenced, self.brace = s, 0, positional, lookup, referenced, brace
        self.words: list[ShellWord] = []
        self._reset()

    def _reset(self) -> None:
        self.cur: list[str] = []
        self.cur_unknown: list[tuple[str, bool]] = []
        self.cur_before_eq = False
        self.has_word = False  # a quoted empty string still makes a word
        self.touched = False  # something (even an empty expansion) is part of the current word
        self.cur_origin = -1

    def _flush(self) -> None:
        if self.has_word or self.cur or self.cur_unknown:
            text = "".join(self.cur)
            self.words.append(ShellWord(text, self.cur_origin, list(self.cur_unknown), self.cur_before_eq))
        self._reset()

    def _text(self, t: str) -> None:
        self.cur.append(t)
        self.has_word = True
        self.touched = True

    def _unknown(self, name: str, quoted: bool) -> None:
        if "=" not in "".join(self.cur):
            self.cur_before_eq = True
        self.cur_unknown.append((name, quoted))
        self.has_word = True
        self.touched = True

    def run(self) -> list[ShellWord]:
        s = self.s
        while self.i < len(s):
            ch = s[self.i]
            if ch in " \t":
                self._flush()
                self.i += 1
            elif ch == "#" and not self.touched:
                while self.i < len(s) and s[self.i] != "\n":
                    self.i += 1
            elif ch == "\n":
                if s[self.i:].strip():
                    raise _Opaque("the script runs more than one command")
                self.i += 1
            elif ch in "<>":
                self._redirect()
            elif ch in _OPERATORS:
                raise _Opaque(f"the script uses {ch!r}, which the scanner does not model")
            elif ch == "\\":
                if self.i + 1 < len(s):
                    if s[self.i + 1] == "\n":
                        self.i += 2
                        continue
                    self._text(s[self.i + 1])
                self.i += 2
            elif ch == "'":
                end = s.find("'", self.i + 1)
                if end < 0:
                    raise _Opaque("a quote in the script is not closed")
                self._text(s[self.i + 1:end])
                self.i = end + 1
            elif ch == '"':
                self._double()
            elif ch == "$":
                self._dollar(quoted=False)
            elif ch == "{" and self.brace and self._brace_expansion():
                raise _Opaque("the script uses brace expansion")
            else:
                self._text(ch)
                self.i += 1
        self._flush()
        return self.words

    def _brace_expansion(self) -> bool:
        end = self.s.find("}", self.i)
        if end < 0:
            return False
        body = self.s[self.i + 1:end]
        return not any(c in body for c in " \t'\"") and ("," in body or ".." in body)

    def _redirect(self) -> None:
        """`[n]>file`, `>>file`, `<file`, `n>&m`: the redirection and its target are not arguments."""
        if self.cur and "".join(self.cur).isdigit() and not self.cur_unknown:
            self._reset()  # the descriptor number belongs to the redirection
        else:
            self._flush()
        s = self.s
        self.i += 1
        while self.i < len(s) and s[self.i] in "<>&|":
            self.i += 1
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        start = self.i
        while self.i < len(s) and s[self.i] not in " \t\n;&|()<>":
            if s[self.i] in "$`'\"\\":
                raise _Opaque("a redirection target in the script is not a plain word")
            self.i += 1
        if self.i == start:
            raise _Opaque("a redirection in the script has no target")

    def _double(self) -> None:
        s = self.s
        self.i += 1
        had_word = self.has_word or bool(self.cur) or bool(self.cur_unknown)
        if not had_word and len(self.pos) <= 1 and (s.startswith('$@"', self.i) or s.startswith('${@}"', self.i)):
            end = s.index('"', self.i) + 1
            if end >= len(s) or s[end] in " \t\n":
                self.i = end  # "$@" with no arguments produces no word
                self.touched = True
                return
        self.has_word = True
        self.touched = True
        while True:
            if self.i >= len(s):
                raise _Opaque("a quote in the script is not closed")
            ch = s[self.i]
            if ch == '"':
                self.i += 1
                return
            if ch == "`":
                raise _Opaque("the script uses command substitution")
            if ch == "\\" and self.i + 1 < len(s) and s[self.i + 1] in '$`"\\\n':
                if s[self.i + 1] != "\n":
                    self._text(s[self.i + 1])
                self.i += 2
                continue
            if ch == "$":
                self._dollar(quoted=True)
                continue
            self._text(ch)
            self.i += 1

    def _dollar(self, quoted: bool) -> None:
        s = self.s
        j = self.i + 1
        if j >= len(s):
            self._text("$")
            self.i = j
            return
        c = s[j]
        if c == "(":
            raise _Opaque("the script uses command substitution or arithmetic")
        if c in "@*":
            self.i = j + 1
            self._positional_all(quoted=quoted, star=c == "*")
            return
        if c == "#":
            self.i = j + 1
            self._value(str(max(len(self.pos) - 1, 0)), quoted, origin=-1)
            return
        if c.isdigit():
            self.i = j + 1
            self._value(self.pos[int(c)] if int(c) < len(self.pos) else None, quoted, origin=int(c))
            return
        if c == "{":
            end = s.find("}", j)
            if end < 0:
                raise _Opaque("a parameter expansion in the script is not closed")
            body = s[j + 1:end]
            self.i = end + 1
            self._braced(body, quoted)
            return
        if c in _NAME_START:
            k = j
            while k < len(s) and s[k] in _NAME_CHARS:
                k += 1
            self.i = k
            self._var(s[j:k], quoted, None)
            return
        if c in "?$!-":
            raise _Opaque(f"the script uses the special parameter ${c}")
        self._text("$")
        self.i = j

    def _braced(self, body: str, quoted: bool) -> None:
        if body in ("@", "*"):
            self._positional_all(quoted=quoted, star=body == "*")
            return
        if body == "#":
            self._value(str(max(len(self.pos) - 1, 0)), quoted, origin=-1)
            return
        if body.isdigit():
            n = int(body)
            self._value(self.pos[n] if n < len(self.pos) else None, quoted, origin=n)
            return
        k = 0
        while k < len(body) and body[k] in _NAME_CHARS:
            k += 1
        name, rest = body[:k], body[k:]
        if not name or name[0] not in _NAME_START:
            raise _Opaque(f"the script uses ${{{body}}}, which the scanner does not model")
        if rest == "":
            self._var(name, quoted, None)
            return
        for op in (":-", "-"):
            if rest.startswith(op):
                word = rest[len(op):]
                if any(ch in word for ch in "$`\\'\"{}"):
                    raise _Opaque(f"the default in ${{{body}}} is not a plain word")
                self._var(name, quoted, (word, op.startswith(":")))
                return
        raise _Opaque(f"the script uses ${{{body}}}, which the scanner does not model")

    def _var(self, name: str, quoted: bool, default: tuple[str, bool] | None) -> None:
        if name not in self.referenced:
            self.referenced.append(name)
        value, known = self.lookup(name)
        if not known:
            self._unknown(name, quoted)
            return
        if default is not None:
            word, colon = default
            if value is None or (colon and value == ""):
                value = word
        self._value(value, quoted, origin=-1)

    def _value(self, value: str | None, quoted: bool, origin: int) -> None:
        self.touched = True
        if origin >= 0 and not self.cur and not self.cur_unknown and not self.has_word:
            self.cur_origin = origin
        if value is None:
            value = ""
        if quoted:
            self._text(value)
            return
        parts = [p for p in _IFS.split(value) if p]
        if not parts:
            return
        if _IFS.match(value[:1]):
            self._flush()
            self.touched = True
        for n, p in enumerate(parts):
            if n:
                self._flush()
                if origin >= 0:
                    self.cur_origin = origin
            self._text(p)
        if _IFS.match(value[-1:]):
            self._flush()

    def _positional_all(self, quoted: bool, star: bool) -> None:
        args = self.pos[1:]
        self.touched = True
        if quoted and not star:
            for n, a in enumerate(args, start=1):
                if n > 1:
                    self._flush()
                if not self.cur:
                    self.cur_origin = n
                self._text(a)
            return
        if quoted and star:
            self._text(" ".join(args))
            return
        first = True
        for n, a in enumerate(args, start=1):
            pieces = [p for p in _IFS.split(a) if p]
            if not pieces:
                continue
            if _IFS.match(a[:1]):
                self._flush()
            for p in pieces:
                if not first:
                    self._flush()
                if not self.cur and not self.has_word:
                    self.cur_origin = n
                self._text(p)
                first = False
            if _IFS.match(a[-1:]):
                self._flush()
