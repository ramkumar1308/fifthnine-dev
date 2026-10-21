"""Support classification against the packaged support matrix (D9).

tested      : version listed under tested_versions in data/support_matrix.v1.yaml
untested    : inside the machine-readable range, or version not determined
unsupported : outside the range; rules that depend on it are not run
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from fifthnine.core.safeparse import load_yaml_documents
from fifthnine.schema.support_v1 import ComponentEntry, SupportMatrix
from fifthnine.schema.v1 import ComponentSupport, SourceRef, SupportLevel


def _plain(value: object) -> object:
    """Convert ruamel containers to plain Python containers for Pydantic."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


@lru_cache(maxsize=1)
def load_matrix() -> SupportMatrix:
    text = resources.files("fifthnine.data").joinpath("support_matrix.v1.yaml").read_text(encoding="utf-8")
    return SupportMatrix.model_validate(_plain(load_yaml_documents(text)[0].data))


def entry_for(component: str) -> ComponentEntry | None:
    return next((c for c in load_matrix().components if c.component == component), None)


def _in_allowed(version: str, allowed: list[str]) -> bool:
    return any(version == a or version.startswith(a + ".") for a in allowed)


def in_range(component: str, version: str) -> bool | None:
    """True/False when the matrix can decide, None when it has no machine-readable range or the version
    string is not comparable."""
    entry = entry_for(component)
    if entry is None:
        return None
    if entry.allowed_versions:
        return _in_allowed(version, entry.allowed_versions)
    if entry.specifier:
        try:
            return Version(version) in SpecifierSet(entry.specifier)
        except (InvalidVersion, InvalidSpecifier):
            return None
    return None


def classify(component: str, version: str | None, source: SourceRef | None = None) -> ComponentSupport:
    entry = entry_for(component)
    tested = [e.version for e in entry.tested_versions] if entry else []
    if version is None:
        level = SupportLevel.UNTESTED
    elif version in tested:
        level = SupportLevel.TESTED
    elif in_range(component, version) is False:
        level = SupportLevel.UNSUPPORTED
    else:
        level = SupportLevel.UNTESTED
    return ComponentSupport(component=component, version=version, version_source=source, support=level,
                            tested_versions=tested, matrix_entry=entry.id if entry else None)
