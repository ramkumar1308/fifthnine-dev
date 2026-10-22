"""Versioned framework defaults tables (data/defaults/*.yaml). Every entry carries its upstream citation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from fifthnine.core.safeparse import load_yaml_documents, to_plain
from fifthnine.schema.v1 import DefaultRef, Reference, UpstreamRef


@dataclass(frozen=True)
class DefaultEntry:
    key: str
    value: Any
    unit: str | None
    ref: DefaultRef
    applies_when: dict[str, Any]


@cache
def kubernetes_defaults() -> dict[str, DefaultEntry]:
    text = resources.files("fifthnine.data").joinpath("defaults/kubernetes.v1.yaml").read_text(encoding="utf-8")
    data = to_plain(load_yaml_documents(text)[0].data)
    out: dict[str, DefaultEntry] = {}
    for key, e in data["entries"].items():
        upstream = UpstreamRef(repository=data["repository"], tag=data["tag"], commit=data["commit"], path=e["path"],
                               line=e["line"], anchor=e["anchor"], verified_at=list(data["verified_at"]))
        url = f"https://github.com/{data['repository']}/blob/{data['commit']}/{e['path']}#L{e['line']}"
        dref = DefaultRef(
            framework=data["framework"], version_range=data["version_range"],
            reference=Reference(title=f"Kubernetes {data['tag']} {e['path']}:{e['line']}", url=url, source_path=f"{data['tag']}:{e['path']}"),
            applied_version=None, upstream=upstream,
        )
        out[key] = DefaultEntry(key, e["value"], e.get("unit"), dref, dict(e.get("applies_when") or {}))
    return out
