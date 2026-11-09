"""Spring Boot configuration files as flattened, line-located documents (M2-2).

`application.yml`, `application.yaml` and `application.properties` (and their `-{profile}` variants) are read into
`ConfigDocument`s. A YAML file is read the way Spring Boot reads it: SnakeYAML's YAML 1.1 core scalar types (so an
unquoted `off` is a boolean and `0755` is an octal integer), nested maps flattened to dotted keys, sequences to indexed
keys (`a[0]`), a YAML null to the empty string, one document per `---`. A properties file may hold several documents
separated by `#---`. Document activation (`spring.config.activate.*`) and import (`spring.config.import`) are lifted out
of the entries so the resolver sees them as conditions, not as properties.

Nothing is evaluated or constructed: YAML goes through the hardened composer (nesting, size and document caps), aliases
are refused, and values stay as written until the resolver converts them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from fifthnine.core.safeparse import ParseError, compose_yaml_documents
from fifthnine.extract.properties import parse_properties

MAX_ENTRIES = 100_000
ACTIVATE_PREFIX = "spring.config.activate."

_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_BOOL = re.compile(r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$")
_INT = re.compile(r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$")
_FLOAT = re.compile(r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*)$")


@dataclass(frozen=True)
class ConfigEntry:
    key: str  # flattened as written: nested maps joined with '.', sequence items as key[0]
    value: Any  # str, int, float or bool; never None (a YAML null is the empty string)
    line: int  # line of the key (or of the sequence item) that holds the value


@dataclass
class ConfigDocument:
    path: str
    index: int  # position of the document inside the file
    start_line: int
    fmt: str  # yaml | properties
    entries: list[ConfigEntry] = field(default_factory=list)
    on_profile: str | None = None  # raw spring.config.activate.on-profile expression, items joined with commas
    on_profile_line: int | None = None
    other_conditions: list[tuple[str, str, int]] = field(default_factory=list)  # (key, value, line) of activate keys not evaluated
    imports: list[tuple[str, int]] = field(default_factory=list)  # spring.config.import values with their lines
    legacy_profiles_selector: tuple[str, int] | None = None  # spring.profiles in a document: rejected by Boot 3
    prefixes: dict[str, int] = field(default_factory=dict)  # every dotted prefix of an entry key -> line of its first occurrence

    @property
    def conditional(self) -> bool:
        return self.on_profile is not None or bool(self.other_conditions)


def _yaml11_scalar(node: ScalarNode) -> Any:
    text = str(node.value)
    if node.style in ("'", '"', "|", ">"):
        return text
    if _NULL.match(text):
        return ""
    if _BOOL.match(text):
        return text.lower() in ("yes", "true", "on")
    if _INT.match(text):
        return _int11(text)
    if _FLOAT.match(text):
        try:
            return float(text.replace("_", "")) if ":" not in text else text
        except ValueError:
            return text
    return text


def _int11(text: str) -> Any:
    t = text.replace("_", "")
    sign = -1 if t.startswith("-") else 1
    t = t.lstrip("+-")
    if ":" in t:
        total = 0
        for part in t.split(":"):
            total = total * 60 + int(part)
        return sign * total
    if t.lower().startswith("0x"):
        return sign * int(t, 16)
    if t.lower().startswith("0b"):
        return sign * int(t[2:], 2)
    if len(t) > 1 and t.startswith("0"):
        return sign * int(t, 8)
    return sign * int(t)


def _flatten(node: Node, prefix: str, out: list[ConfigEntry], seen: set[int], key_line: int, prefixes: dict[str, int]) -> None:
    if len(out) > MAX_ENTRIES:
        raise ParseError(f"more than {MAX_ENTRIES} configuration entries in one document")
    line = node.start_mark.line + 1
    if isinstance(node, (MappingNode, SequenceNode)):
        if id(node) in seen:
            raise ParseError("YAML aliases (reused anchors) are not accepted in Spring configuration files read by M2")
        seen.add(id(node))
    if isinstance(node, MappingNode):
        for k, v in node.value:
            if not isinstance(k, ScalarNode):
                raise ParseError(f"non-scalar mapping key at line {k.start_mark.line + 1}")
            name = str(k.value)
            child = f"{prefix}.{name}" if prefix else name
            _record_prefixes(child, k.start_mark.line + 1, prefixes)
            _flatten(v, child, out, seen, k.start_mark.line + 1, prefixes)
        return
    if isinstance(node, SequenceNode):
        for i, item in enumerate(node.value):
            _flatten(item, f"{prefix}[{i}]", out, seen, item.start_mark.line + 1, prefixes)
        return
    if isinstance(node, ScalarNode):
        if prefix:
            out.append(ConfigEntry(prefix, _yaml11_scalar(node), key_line))
        return
    raise ParseError(f"unsupported YAML node at line {line}")


def _record_prefixes(key: str, line: int, prefixes: dict[str, int]) -> None:
    # a YAML key may itself contain dots (`a.b: 1`); every dotted prefix of the flattened name is recorded
    parts = key.split(".")
    for i in range(1, len(parts) + 1):
        prefixes.setdefault(".".join(parts[:i]), line)


def _lift(doc: ConfigDocument, entries: list[tuple[str, Any, int]]) -> None:
    on_profile: list[tuple[str, int]] = []
    for key, value, line in entries:
        if key.startswith(ACTIVATE_PREFIX):
            tail = key[len(ACTIVATE_PREFIX):]
            base = re.sub(r"\[\d+\]$", "", tail)
            if base.replace("_", "-").lower() == "on-profile":
                on_profile.append((str(value), line))
            else:
                doc.other_conditions.append((key, str(value), line))
            continue
        if re.sub(r"\[\d+\]$", "", key).replace("_", "-").lower() == "spring.config.import":
            doc.imports.append((str(value), line))
            continue
        if re.sub(r"\[\d+\]$", "", key) == "spring.profiles" and doc.legacy_profiles_selector is None:
            doc.legacy_profiles_selector = (str(value), line)
        doc.entries.append(ConfigEntry(key, value, line))
    if on_profile:
        doc.on_profile = ",".join(v for v, _ in on_profile)
        doc.on_profile_line = on_profile[0][1]


def parse_config_file(path: str, text: str) -> list[ConfigDocument]:
    """Documents of one configuration file. Raises ParseError for malformed YAML, aliases and over-large documents."""
    lowered = path.lower()
    docs: list[ConfigDocument] = []
    if lowered.endswith(".properties"):
        for i, pdoc in enumerate(parse_properties(text)):
            doc = ConfigDocument(path=path, index=i, start_line=pdoc.start_line, fmt="properties")
            _lift(doc, [(e.key, e.value, e.line) for e in pdoc.entries])
            for e in doc.entries:
                _record_prefixes(e.key, e.line, doc.prefixes)
            if doc.entries or doc.conditional or doc.imports:
                docs.append(doc)
        for i, doc in enumerate(docs):
            doc.index = i
        return docs
    for node in compose_yaml_documents(text):
        flat: list[ConfigEntry] = []
        prefixes: dict[str, int] = {}
        _flatten(node, "", flat, set(), node.start_mark.line + 1, prefixes)
        doc = ConfigDocument(path=path, index=len(docs), start_line=node.start_mark.line + 1, fmt="yaml", prefixes=prefixes)
        _lift(doc, [(e.key, e.value, e.line) for e in flat])
        docs.append(doc)
    return docs


# ---------------------------------------------------------------- profile expressions (Profiles.of)


class ProfileExpressionError(ValueError):
    """The expression is not valid Spring profile syntax, or is mixed `&`/`|` without parentheses."""


def evaluate_profile_expression(expression: str, active: set[str]) -> bool:
    """Spring Boot `Profiles.of(...)`: a comma-separated list matches when any item matches; an item is a profile
    name, `!name`, `(...)`, and `a & b` or `a | b` (not mixed without parentheses)."""
    if len(expression) > 1024:
        raise ProfileExpressionError("profile expression longer than 1024 characters")
    return any(_Parser(item).evaluate(active) for item in _split_commas(expression))


def _split_commas(expression: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in expression:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    items = [p.strip() for p in parts if p.strip()]
    if not items:
        raise ProfileExpressionError("empty profile expression")
    return items


_TOKEN = re.compile(r"\s*([()!&|]|[^()!&|\s]+)")


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _TOKEN.findall(text)
        if "".join(self.tokens).replace(" ", "") != re.sub(r"\s+", "", text):
            raise ProfileExpressionError(f"cannot parse profile expression {text!r}")
        self.pos = 0

    def evaluate(self, active: set[str]) -> bool:
        value = self._expr(active)
        if self.pos != len(self.tokens):
            raise ProfileExpressionError("unexpected tokens in profile expression")
        return value

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _expr(self, active: set[str]) -> bool:
        first = self._term(active)
        op = self._peek()
        if op not in ("&", "|"):
            return first
        values = [first]
        while self._peek() == op:
            self.pos += 1
            values.append(self._term(active))
        if self._peek() in ("&", "|"):
            raise ProfileExpressionError("'&' and '|' cannot be mixed without parentheses")
        return all(values) if op == "&" else any(values)

    def _term(self, active: set[str]) -> bool:
        tok = self._peek()
        if tok is None:
            raise ProfileExpressionError("profile expression ends early")
        self.pos += 1
        if tok == "!":
            return not self._term(active)
        if tok == "(":
            value = self._expr(active)
            if self._peek() != ")":
                raise ProfileExpressionError("missing ')' in profile expression")
            self.pos += 1
            return value
        if tok in (")", "&", "|"):
            raise ProfileExpressionError(f"unexpected {tok!r} in profile expression")
        return tok in active
