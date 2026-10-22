"""Support classification against the packaged support matrix (D9).

tested      : version listed under tested_versions in data/support_matrix.v1.yaml
untested    : inside the machine-readable range, or version not determined
unsupported : outside the range; rules that depend on it are not run
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from fifthnine.core.safeparse import load_yaml_documents, to_plain
from fifthnine.schema.support_v1 import ComponentEntry, SupportMatrix
from fifthnine.schema.v1 import ComponentSupport, SourceRef, SupportLevel


@lru_cache(maxsize=1)
def load_matrix() -> SupportMatrix:
    text = resources.files("fifthnine.data").joinpath("support_matrix.v1.yaml").read_text(encoding="utf-8")
    return SupportMatrix.model_validate(to_plain(load_yaml_documents(text)[0].data))


def entry_for(component: str) -> ComponentEntry | None:
    return next((c for c in load_matrix().components if c.component == component), None)


def _in_allowed(version: str, allowed: list[str]) -> bool:
    return any(version == a or version.startswith(a + ".") for a in allowed)


_NUMERIC_PREFIX = re.compile(r"^v?(\d+(?:\.\d+)*)")


def comparable_version(version: str) -> Version | None:
    """PEP 440 view of a version string. Maven-style qualifiers (2.3.4.RELEASE, 4.0.0-SNAPSHOT, 3.4.0-M1,
    5.3.39.Final) are compared by their numeric release part, so out-of-range releases are never shown as
    untested just because their qualifier is not PEP 440. None when there is no numeric release part."""
    try:
        return Version(version)
    except InvalidVersion:
        match = _NUMERIC_PREFIX.match(version)
        return Version(match.group(1)) if match else None


def in_range(component: str, version: str) -> bool | None:
    """True/False when the matrix can decide, None when it has no machine-readable range or the version
    string has no comparable numeric part."""
    entry = entry_for(component)
    if entry is None:
        return None
    if entry.allowed_versions:
        return _in_allowed(version, entry.allowed_versions)
    if entry.specifier:
        parsed = comparable_version(version)
        if parsed is None:
            return None
        try:
            return parsed in SpecifierSet(entry.specifier, prereleases=True)
        except InvalidSpecifier:
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
