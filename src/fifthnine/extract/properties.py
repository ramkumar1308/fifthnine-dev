"""Java .properties files with 1-based source lines (M2-2).

Follows `java.util.Properties.load` and Spring Boot's `OriginTrackedPropertiesLoader`: comment lines start with `#` or
`!`, a logical line continues while the physical line ends in an odd number of backslashes, the key ends at the first
unescaped `=`, `:` or whitespace, and the usual escapes (`\\t \\n \\r \\f \\uXXXX`) are decoded. A line that is exactly
`#---` (or `!---`) starts a new document, as in Spring Boot multi-document properties. A repeated key keeps the last
value. Nothing is executed or interpolated here; placeholders are resolved later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DOCUMENT_SEPARATOR = re.compile(r"^[#!]---\s*$")
MAX_LOGICAL_LINE = 1 << 20  # the file cap already bounds this; kept so a future caller cannot build a huge string


@dataclass(frozen=True)
class PropertyEntry:
    key: str
    value: str
    line: int  # first physical line of the logical line


@dataclass
class PropertiesDocument:
    start_line: int
    entries: list[PropertyEntry] = field(default_factory=list)


_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 >= len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2:i + 6] or ""):
            out.append(chr(int(text[i + 2:i + 6], 16)))
            i += 6
        else:
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
    return "".join(out)


def _split_key_value(logical: str) -> tuple[str, str]:
    """Key and value of one logical line (leading whitespace already removed)."""
    i = 0
    n = len(logical)
    while i < n:
        ch = logical[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "=: \t\f":
            break
        i += 1
    key = logical[:i]
    while i < n and logical[i] in " \t\f":
        i += 1
    if i < n and logical[i] in "=:":
        i += 1
    while i < n and logical[i] in " \t\f":
        i += 1
    return _unescape(key), _unescape(logical[i:])


def parse_properties(text: str) -> list[PropertiesDocument]:
    """Documents of a .properties file (one unless `#---` separators are present), each with its entries in file
    order. A repeated key inside one document keeps its last value and last line."""
    docs: list[PropertiesDocument] = [PropertiesDocument(start_line=1)]
    seen: list[dict[str, PropertyEntry]] = [{}]  # per document: key -> last entry (dict keeps first-insertion order)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        number = i + 1
        stripped = raw.lstrip(" \t\f")
        if DOCUMENT_SEPARATOR.match(raw):
            docs.append(PropertiesDocument(start_line=number + 1))
            seen.append({})
            i += 1
            continue
        if not stripped or stripped[0] in "#!":
            i += 1
            continue
        parts = [stripped]
        size = len(stripped)
        while _ends_with_odd_backslashes(parts[-1]) and i + 1 < len(lines) and size < MAX_LOGICAL_LINE:
            parts[-1] = parts[-1][:-1]
            i += 1
            parts.append(lines[i].lstrip(" \t\f"))
            size += len(parts[-1])
        if _ends_with_odd_backslashes(parts[-1]):
            parts[-1] = parts[-1][:-1]
        key, value = _split_key_value("".join(parts))
        if key:
            seen[-1].pop(key, None)  # re-insert so the entry sits at its last position
            seen[-1][key] = PropertyEntry(key, value, number)
        i += 1
    for doc, entries in zip(docs, seen, strict=True):
        doc.entries = list(entries.values())
    return docs


def _ends_with_odd_backslashes(s: str) -> bool:
    count = len(s) - len(s.rstrip("\\"))
    return count % 2 == 1
