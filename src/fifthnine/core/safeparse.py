"""Parsers for untrusted repository files.

Repository content is hostile input (contract section 11). Every parser here:
- reads only text the workspace walker already size-capped,
- never resolves external entities, includes, tags or object constructors,
- raises ParseError (never anything else) on malformed or refused input.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any
from xml.parsers import expat

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib

MAX_YAML_DOCUMENTS = 200
MAX_XML_ELEMENTS = 100_000


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


@dataclass
class YamlDocument:
    index: int
    data: Any
    start_line: int


def load_yaml_documents(text: str) -> list[YamlDocument]:
    """Load all documents with ruamel's round-trip loader, which keeps line numbers and builds only plain
    mappings, sequences and scalars (no arbitrary Python objects). Aliases become shared references, not copies."""
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
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise ParseError(f"invalid YAML{where}: {getattr(exc, 'problem', None) or type(exc).__name__}") from None
    except RecursionError:
        raise ParseError("YAML nesting too deep") from None
    return docs


def yaml_key_line(mapping: Any, key: str) -> int | None:
    """1-based line of a key in a ruamel mapping, if known."""
    try:
        line, _col = mapping.lc.key(key)
        return int(line) + 1
    except (AttributeError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- TOML


def load_toml(text: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = tomllib.loads(text)
        return data
    except tomllib.TOMLDecodeError as exc:
        raise ParseError(f"invalid TOML: {exc}") from None


# ---------------------------------------------------------------- helpers


def find_line(text: str, needle: str, start: int = 1) -> int | None:
    """1-based number of the first line at or after `start` containing `needle` verbatim. Used to locate values
    that a parser without position information (TOML, requirements files) has already extracted."""
    for number, line in enumerate(text.splitlines(), 1):
        if number >= start and needle in line:
            return number
    return None
