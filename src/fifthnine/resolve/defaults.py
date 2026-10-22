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


# ---------------------------------------------------------------- Spring Boot (M2-2)


@dataclass(frozen=True)
class SpringDefault:
    key: str
    value: Any
    unit: str | None
    ref: DefaultRef
    applies_when: dict[str, Any]
    minor: str


@cache
def _spring_table() -> dict[str, Any]:
    text = resources.files("fifthnine.data").joinpath("defaults/spring-boot.v1.yaml").read_text(encoding="utf-8")
    table: dict[str, Any] = to_plain(load_yaml_documents(text)[0].data)
    return table


def spring_minor(version: str | None) -> str | None:
    """`3.4.13` -> `3.4` when that minor has a reviewed release in the table, else None. A version outside the
    table never borrows another minor's defaults."""
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    minor = f"{int(parts[0])}.{int(parts[1])}"
    return minor if minor in _spring_table()["releases"] else None


def spring_minors() -> list[str]:
    return sorted(_spring_table()["releases"])


def spring_default(key: str, version: str | None) -> SpringDefault | None:
    """The cited default of `key` for the detected Boot `version`, or None when the version is not covered or the table
    has no entry. `component.spring-framework` is exact-patch only: it answers only for the reviewed patch release."""
    table = _spring_table()
    minor = spring_minor(version)
    entry = table["entries"].get(key)
    if minor is None or entry is None or version is None:
        return None
    row = entry["versions"].get(minor)
    release = table["releases"][minor]
    if row is None:
        return None
    if entry.get("exact_patch_only") and f"v{version}" != release["tag"]:
        return None
    upstream = UpstreamRef(repository=table["repository"], tag=release["tag"], commit=release["commit"], path=row["path"],
                           line=row["line"], anchor=row["anchor"],
                           verified_at=[release["tag"]] if entry.get("exact_patch_only") else list(release.get("verified_at") or [release["tag"]]))
    url = f"https://github.com/{table['repository']}/blob/{release['commit']}/{row['path']}#L{row['line']}"
    dref = DefaultRef(
        framework=table["framework"], version_range=f"{minor}.x",
        reference=Reference(title=f"Spring Boot {release['tag']} {row['path']}:{row['line']}", url=url,
                            source_path=f"{release['tag']}:{row['path']}"),
        applied_version=version, upstream=upstream,
    )
    return SpringDefault(key, row["value"], entry.get("unit"), dref, dict(entry.get("applies_when") or {}), minor)
