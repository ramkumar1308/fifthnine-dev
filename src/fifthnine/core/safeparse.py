"""Parsers for untrusted repository files.

Repository content is hostile input (contract section 11). Every parser here:
- reads only text the workspace walker already size-capped,
- never resolves external entities, includes, tags or object constructors,
- raises ParseError (never anything else) on malformed or refused input.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any
from xml.parsers import expat

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib

MAX_YAML_DOCUMENTS = 200
MAX_XML_ELEMENTS = 100_000
MAX_PLAIN_NODES = 100_000
_QUOTED = re.compile(r"""("[^"]*"|'[^']*')""")


def _redact(message: str) -> str:
    """Replace quoted values in parser messages, which may contain secrets."""
    return _QUOTED.sub("'****'", message)


class ParseError(Exception):
    """The file could not be parsed safely. The message is safe to show to users."""


# ---------------------------------------------------------------- XML


@dataclass
class XmlNode:
    tag: str
    line: int
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list[XmlNode] = field(default_factory=list)

    def child(self, tag: str) -> XmlNode | None:
        return next((c for c in self.children if c.tag == tag), None)

    def children_named(self, tag: str) -> list[XmlNode]:
        return [c for c in self.children if c.tag == tag]

    def child_text(self, tag: str) -> str | None:
        node = self.child(tag)
        return node.text.strip() if node is not None else None


def parse_xml(text: str) -> XmlNode:
    """Parse XML with line numbers. DOCTYPE and entity declarations are refused outright,
    which blocks entity expansion (billion laughs) and external entity resolution."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[XmlNode] = []
    root: list[XmlNode] = []
    count = 0

    def refuse(*_args: object) -> None:
        raise ParseError("XML document type declarations are not accepted")

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal count
        count += 1
        if count > MAX_XML_ELEMENTS:
            raise ParseError(f"XML has more than {MAX_XML_ELEMENTS} elements")
        node = XmlNode(tag=name.rsplit("}", 1)[-1].rsplit(":", 1)[-1], line=parser.CurrentLineNumber, attrs=dict(attrs))
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_name: str) -> None:
        stack.pop()

    def chars(data: str) -> None:
        if stack:
            stack[-1].text += data

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse  # type: ignore[assignment]
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(text, True)
    except expat.ExpatError as exc:
        raise ParseError(f"invalid XML: {expat.errors.messages.get(exc.code, 'syntax error')} at line {exc.lineno}") from None
    if not root:
        raise ParseError("empty XML document")
    return root[0]


# ---------------------------------------------------------------- YAML


def _yaml_error(exc: BaseException) -> ParseError:
    """Location and problem class only. Quoted values in parser messages are redacted, because they can be
    secrets (for example a duplicated `password` key)."""
    mark = getattr(exc, "problem_mark", None)
    where = f" at line {mark.line + 1}" if mark is not None else ""
    problem = getattr(exc, "problem", None) or type(exc).__name__
    return ParseError(f"invalid YAML{where}: {_redact(str(problem))}")


@dataclass
class YamlDocument:
    index: int
    data: Any
    start_line: int


def load_yaml_documents(text: str) -> list[YamlDocument]:
    """Load documents with ruamel's round-trip loader (plain mappings, sequences and scalars; no arbitrary Python
    objects; aliases stay shared references). Used for .fifthnine.yml and packaged data. Convert with to_plain()
    before handing data to anything that copies it."""
    _check_yaml_depth(text)
    yaml = YAML(typ="rt", pure=True)
    yaml.allow_duplicate_keys = False
    docs: list[YamlDocument] = []
    try:
        for i, data in enumerate(yaml.load_all(text)):
            if i >= MAX_YAML_DOCUMENTS:
                raise ParseError(f"more than {MAX_YAML_DOCUMENTS} YAML documents")
            line = getattr(getattr(data, "lc", None), "line", None)
            docs.append(YamlDocument(index=i, data=data, start_line=(line + 1) if isinstance(line, int) else 1))
    except YAMLError as exc:
        raise _yaml_error(exc) from None
    except RecursionError:
        raise ParseError("YAML nesting too deep") from None
    return docs


MAX_YAML_DEPTH = 200
_BLOCK_SCALAR_HEADER = re.compile(r"(^|:\s+|-\s+)[|>][-+0-9]*\s*$")
_NEST_TOKENS = ("- ", "? ", ": ")


def _flow_brackets(line: str) -> tuple[int, int]:
    """(opened, closed) flow brackets on one line, ignoring quoted text and a trailing comment."""
    opened = closed = 0
    quote = ""
    prev = " "
    for ch in line:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and prev in " \t[{,:-?":
            quote = ch
        elif ch == "#" and prev in " \t":
            break
        elif ch in "[{":
            opened += 1
        elif ch in "]}":
            closed += 1
        prev = ch
    return opened, closed


def _check_yaml_depth(text: str) -> None:
    """Refuse deeply nested YAML before the parser sees it. Deep nesting can exhaust the C stack of the composer
    (a hard crash, not a RecursionError), so depth is bounded by a linear scan: flow brackets (outside quotes and
    comments), block indentation, and compact nesting tokens (`- `, `? `, `: `) at the start of a line. Block scalar
    bodies are skipped, and the count restarts at each document marker."""
    flow = 0
    stack: list[int] = []  # block nesting: one entry per increase of indentation
    scalar_indent: int | None = None  # inside a block scalar whose header is at this indent
    for line in text.splitlines():
        body = line.lstrip(" ")
        if not body:
            continue
        indent = len(line) - len(body)
        if scalar_indent is not None:
            if indent > scalar_indent:
                continue
            scalar_indent = None
        if body.startswith("#"):
            continue
        if indent == 0 and (body.startswith("---") or body.startswith("...")):
            flow = 0
            stack.clear()
            body = body[3:].lstrip(" ")
            if not body:
                continue
        tokens = 0
        while body[:2] in _NEST_TOKENS:  # compact nesting: "- - - a", "? ? ? a"
            tokens += 1
            body = body[2:].lstrip(" ")
        opened, closed = _flow_brackets(body)
        flow = max(0, flow + opened - closed)
        while stack and indent <= stack[-1]:
            stack.pop()
        stack.append(indent)
        if flow + opened > MAX_YAML_DEPTH or len(stack) + tokens + flow > MAX_YAML_DEPTH or tokens > MAX_YAML_DEPTH:
            raise ParseError("YAML nesting too deep")
        if _BLOCK_SCALAR_HEADER.search(body.split(" #", 1)[0]):
            scalar_indent = indent


def compose_yaml_documents(text: str) -> list[Node]:
    """Compose (parse without constructing) every document. Uses the libyaml-based C parser when available and
    the pure-Python parser otherwise. Nothing is constructed, so tags never run and aliases are never expanded;
    every node carries its start line. Used to inventory Kubernetes objects, where only a few top-level scalars
    are needed and manifests can be large."""
    _check_yaml_depth(text)
    try:
        yaml = YAML(typ="safe", pure=False)
        nodes: list[Node] = []
        for i, node in enumerate(yaml.compose_all(text)):
            if i >= MAX_YAML_DOCUMENTS:
                raise ParseError(f"more than {MAX_YAML_DOCUMENTS} YAML documents")
            if node is not None:
                nodes.append(node)
        return nodes
    except YAMLError as exc:
        raise _yaml_error(exc) from None
    except RecursionError:
        raise ParseError("YAML nesting too deep") from None


def mapping_scalars(node: Node, *keys: str) -> dict[str, tuple[str, int]]:
    """Scalar values of the given top-level keys of a mapping node, with 1-based key lines. Non-scalar values and
    non-mapping documents give nothing."""
    out: dict[str, tuple[str, int]] = {}
    if not isinstance(node, MappingNode):
        return out
    for key, value in node.value:
        if isinstance(key, ScalarNode) and key.value in keys and isinstance(value, ScalarNode) and key.value not in out:
            out[key.value] = (str(value.value), key.start_mark.line + 1)
    return out


def mapping_child(node: Node, key: str) -> Node | None:
    if isinstance(node, MappingNode):
        for k, v in node.value:
            if isinstance(k, ScalarNode) and k.value == key:
                return v  # type: ignore[no-any-return]
    return None


def to_plain(value: Any, max_nodes: int = MAX_PLAIN_NODES) -> Any:
    """Copy ruamel containers into plain dicts and lists for validation. Refuses YAML aliases and anchors that are
    reused (each container may appear once) and caps the node count, so an alias bomb in a repository-controlled
    file cannot expand into a huge copy."""
    seen: set[int] = set()
    count = 0

    def walk(v: Any) -> Any:
        nonlocal count
        count += 1
        if count > max_nodes:
            raise ParseError(f"more than {max_nodes} YAML nodes")
        if isinstance(v, (dict, list)):
            if id(v) in seen:
                raise ParseError("YAML aliases (reused anchors) are not accepted in this file")
            seen.add(id(v))
            if isinstance(v, dict):
                return {str(k): walk(x) for k, x in v.items()}
            return [walk(x) for x in v]
        return v

    try:
        return walk(value)
    except RecursionError:
        raise ParseError("YAML nesting too deep") from None


# ---------------------------------------------------------------- TOML


def load_toml(text: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = tomllib.loads(text)
        return data
    except tomllib.TOMLDecodeError as exc:
        raise ParseError(f"invalid TOML: {_redact(str(exc))}") from None
    except RecursionError:
        raise ParseError("TOML nesting too deep") from None


# ---------------------------------------------------------------- helpers


def find_line(text: str, needle: str, start: int = 1, comment: str | None = "#") -> int | None:
    """1-based number of the first line at or after `start` containing `needle` verbatim, ignoring lines that are
    comments. Used only where a parser gives no positions (TOML); callers narrow `start` to the right section."""
    for number, line in enumerate(text.splitlines(), 1):
        if number < start:
            continue
        if comment and line.lstrip().startswith(comment):
            continue
        if needle in line:
            return number
    return None


def section_start(text: str, header: str) -> int | None:
    """1-based line of a TOML table header such as `[tool.poetry.dependencies]`."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() == header:
            return number
    return None


def toml_array_table_lines(text: str, header: str, key: str) -> dict[str, int]:
    """For `[[header]]` array tables (lockfiles), map the value of `key` in each table to its line, using only the
    first `key = "..."` line after each header so nested references to the same name are not matched."""
    found: dict[str, int] = {}
    pending = False
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"')
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped == f"[[{header}]]":
            pending = True
            continue
        if stripped.startswith("["):
            pending = False
            continue
        if pending:
            match = pattern.match(line)
            if match:
                found.setdefault(match.group(1), number)
                pending = False
    return found
