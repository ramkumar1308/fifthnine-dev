"""Line-aware YAML values built from composed nodes (no object construction, no alias expansion).

`safeparse.compose_yaml_documents` gives ruamel nodes with start marks. This module turns a node into a small tree
of `Y` values that keep the 1-based line of the value and of the key that holds it, so every extracted fact can cite
the exact line. Aliases were never expanded by the composer; a reused anchor appears as the same node object and is
refused here, as in `to_plain`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from fifthnine.core.safeparse import ParseError

MAX_NODES = 200_000


@dataclass(frozen=True)
class Y:
    """A YAML value. `value` is a dict[str, Y], a list[Y] or a scalar (str, int, float, bool, None).
    `line` is where the value starts; `key_line` is the line of the mapping key that holds it (equal to `line`
    for sequence items and documents)."""

    value: Any
    line: int
    key_line: int
    tag: str = ""

    # -- mapping helpers
    def get(self, key: str) -> Y | None:
        """The child at `key`. A key whose value is YAML null counts as not set: in Kubernetes manifests an explicit
        null and an omitted field mean the same thing, and defaulting applies to both."""
        if isinstance(self.value, dict):
            found: Y | None = self.value.get(key)
            if found is not None and found.value is None:
                return None
            return found
        return None

    def path(self, *keys: str) -> Y | None:
        node: Y | None = self
        for k in keys:
            node = node.get(k) if node is not None else None
        return node

    def scalar(self, key: str) -> Any:
        child = self.get(key)
        return child.value if child is not None and not isinstance(child.value, (dict, list)) else None

    @property
    def is_map(self) -> bool:
        return isinstance(self.value, dict)

    @property
    def is_seq(self) -> bool:
        return isinstance(self.value, list)

    def seq(self) -> list[Y]:
        return list(self.value) if isinstance(self.value, list) else []

    def plain(self) -> Any:
        if isinstance(self.value, dict):
            return {k: v.plain() for k, v in self.value.items()}
        if isinstance(self.value, list):
            return [v.plain() for v in self.value]
        return self.value


def _scalar(node: ScalarNode) -> Any:
    tag = node.tag or ""
    text = node.value
    if tag.endswith(":null"):
        return None
    if tag.endswith(":bool"):
        return str(text).lower() == "true"
    if tag.endswith(":int"):
        try:
            return int(str(text).replace("_", ""), 0)
        except ValueError:
            return str(text)
    if tag.endswith(":float"):
        try:
            return float(text)
        except ValueError:
            return str(text)
    return str(text)


def to_y(node: Node, max_nodes: int = MAX_NODES) -> Y:
    seen: set[int] = set()
    count = 0

    def walk(n: Node, key_line: int | None) -> Y:
        nonlocal count
        count += 1
        if count > max_nodes:
            raise ParseError(f"more than {max_nodes} YAML nodes")
        line = n.start_mark.line + 1
        kl = key_line if key_line is not None else line
        if isinstance(n, (MappingNode, SequenceNode)):
            if id(n) in seen:
                raise ParseError("YAML aliases (reused anchors) are not accepted in Kubernetes manifests read by M2")
            seen.add(id(n))
        if isinstance(n, MappingNode):
            out: dict[str, Y] = {}
            for k, v in n.value:
                if not isinstance(k, ScalarNode):
                    raise ParseError(f"non-scalar mapping key at line {k.start_mark.line + 1}")
                name = str(k.value)
                if name in out:
                    raise ParseError(f"duplicate key {name!r} at line {k.start_mark.line + 1}")
                out[name] = walk(v, k.start_mark.line + 1)
            return Y(out, line, kl, n.tag or "")
        if isinstance(n, SequenceNode):
            return Y([walk(v, None) for v in n.value], line, kl, n.tag or "")
        if isinstance(n, ScalarNode):
            return Y(_scalar(n), line, kl, n.tag or "")
        raise ParseError(f"unsupported YAML node at line {line}")

    try:
        return walk(node, None)
    except RecursionError:
        raise ParseError("YAML nesting too deep") from None
