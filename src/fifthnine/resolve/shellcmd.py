"""A small, deliberately incomplete model of `sh -c "<script>"` as a container runs it.

Only one simple command is modelled: optional leading `exec`, then words built from literal text, single and double
quotes, backslash escapes, `$NAME`, `${NAME}`, `${NAME:-word}`, `${NAME-word}`, `${NAME:=word}`, `${NAME=word}` and the
positional parameters (`$0` to `$9`, `$@`, `$*`, `"$@"`). Anything else (several commands, pipes, redirections,
substitutions, arithmetic, other parameter forms, leading assignments) makes the script opaque, and the caller must
treat every option it could set as unknown. Nothing is executed.

Expansion follows POSIX field splitting with the default IFS: an unquoted expansion splits on blanks and an empty
unquoted expansion produces no word; a quoted expansion is one word. A variable whose value is unknown is recorded on
the word it appears in, with whether it was quoted, so the caller can decide what that word could be.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

SHELLS = frozenset({"sh", "bash", "ash", "dash", "zsh", "ksh"})
_OPERATORS = set(";&|<>()`\n")
_NAME_START = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_NAME_CHARS = _NAME_START | set("0123456789")


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


# lookup(name) -> (value, known). known=False: the value cannot be known. known=True with value None: the variable is unset.
Lookup = Callable[[str], "tuple[str | None, bool]"]


class _Opaque(Exception):
    pass


def expand(script: str, positional: list[str], lookup: Lookup) -> ShellParse:
    referenced: list[str] = []
    try:
        words = _Expander(script, positional, lookup, referenced).run()
    except _Opaque as exc:
        return ShellParse([], opaque=str(exc), referenced=referenced)
    if words and words[0].text == "exec" and not words[0].unknown and words[0].origin == -1:
        words = words[1:]
    if words and not words[0].unknown and "=" in words[0].text and words[0].text.split("=", 1)[0].replace("_", "a").isalnum() \
            and words[0].text[0] in _NAME_START:
        return ShellParse([], opaque="the script assigns variables before the command", referenced=referenced)
    return ShellParse(words, referenced=referenced)


class _Expander:
    def __init__(self, s: str, positional: list[str], lookup: Lookup, referenced: list[str]) -> None:
        self.s, self.i, self.pos, self.lookup, self.referenced = s, 0, positional, lookup, referenced
        self.words: list[ShellWord] = []
        self._reset()

    def _reset(self) -> None:
        self.cur: list[str] = []
        self.cur_unknown: list[tuple[str, bool]] = []
        self.cur_before_eq = False
        self.has_word = False  # a quoted empty string still makes a word
        self.cur_origin = -1

    def _flush(self) -> None:
        if self.has_word or self.cur or self.cur_unknown:
            text = "".join(self.cur)
            self.words.append(ShellWord(text, self.cur_origin, list(self.cur_unknown), self.cur_before_eq))
        self._reset()

    def _text(self, t: str) -> None:
        self.cur.append(t)
        self.has_word = True

    def _unknown(self, name: str, quoted: bool) -> None:
        if "=" not in "".join(self.cur):
            self.cur_before_eq = True
        self.cur_unknown.append((name, quoted))
        self.has_word = True

    def run(self) -> list[ShellWord]:
        s = self.s
        while self.i < len(s):
            ch = s[self.i]
            if ch in " \t":
                self._flush()
                self.i += 1
            elif ch == "#" and not self.has_word and not self.cur:
                while self.i < len(s) and s[self.i] != "\n":
                    self.i += 1
            elif ch == "\n":
                if s[self.i:].strip():
                    raise _Opaque("the script runs more than one command")
                self.i += 1
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
            else:
                self._text(ch)
                self.i += 1
        self._flush()
        return self.words

    def _double(self) -> None:
        s = self.s
        self.i += 1
        self.has_word = True
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
        if c in "?$!#-":
            raise _Opaque(f"the script uses the special parameter ${c}")
        self._text("$")
        self.i = j

    def _braced(self, body: str, quoted: bool) -> None:
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
        for op in (":-", ":=", "-", "="):
            if rest.startswith(op):
                word = rest[len(op):]
                if any(ch in word for ch in "$`\\'\""):
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
        if origin >= 0 and not self.cur and not self.cur_unknown and not self.has_word:
            self.cur_origin = origin
        if value is None:
            value = ""
        if quoted:
            self._text(value)
            return
        parts = value.split()
        if not parts:
            return
        if value[:1] in " \t\n":
            self._flush()
        for n, p in enumerate(parts):
            if n:
                self._flush()
                if origin >= 0:
                    self.cur_origin = origin
            self._text(p)
        if value[-1:] in " \t\n":
            self._flush()

    def _positional_all(self, quoted: bool, star: bool) -> None:
        args = self.pos[1:]
        if quoted and not star:
            if not args:
                return  # "$@" with no arguments produces no word
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
            for p in a.split():
                if not first:
                    self._flush()
                if not self.cur and not self.has_word:
                    self.cur_origin = n
                self._text(p)
                first = False
