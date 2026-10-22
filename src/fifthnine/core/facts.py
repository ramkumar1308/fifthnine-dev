"""Fact registry for M2 extractors: stable ids, provenance helpers, deterministic ordering."""

from __future__ import annotations

import hashlib
from typing import Any

from fifthnine.schema.v1 import (
    DefaultRef,
    ExposureSource,
    Fact,
    FactInput,
    FactStatus,
    InputRole,
    OverrideExposure,
    SourceRef,
    Subject,
)


def ref(path: str, line: int, excerpt: str | None = None) -> SourceRef:
    return SourceRef(path=path, line_start=line, line_end=line, excerpt=excerpt)


def loc(role: InputRole, path: str, line: int) -> FactInput:
    return FactInput(role=role, source=ref(path, line))


def via(role: InputRole, fact_id: str) -> FactInput:
    return FactInput(role=role, fact_id=fact_id)


def fact_id(kind: str, subject: str, scope: dict[str, str]) -> str:
    material = "\x1f".join([kind, subject, *[f"{k}={v}" for k, v in sorted(scope.items())]])
    return "f-" + hashlib.sha256(material.encode()).hexdigest()[:16]


class FactBuilder:
    """Collects facts. A (kind, subject, scope) triple identifies a fact; adding it twice keeps the first and
    records the clash, so duplicated objects never silently replace each other."""

    def __init__(self, extractor: str) -> None:
        self.extractor = extractor
        self._facts: dict[str, Fact] = {}
        self.clashes: list[tuple[str, str]] = []

    def add(
        self,
        kind: str,
        subject: str,
        value: Any,
        status: FactStatus,
        *,
        source: SourceRef | None = None,
        display: str | None = None,
        subject_kind: str | None = None,
        scope: dict[str, str] | None = None,
        unit: str | None = None,
        inputs: list[FactInput] | None = None,
        exposure: OverrideExposure = OverrideExposure.NONE,
        exposure_sources: list[ExposureSource] | None = None,
        link_ids: list[str] | None = None,
        precedence: str | None = None,
        absent: bool = False,
        default_ref: DefaultRef | None = None,
        overridden_by: str | None = None,
        unresolved_reason: str | None = None,
    ) -> str:
        scope = scope or {}
        fid = fact_id(kind, subject, scope)
        if fid in self._facts:
            self.clashes.append((kind, subject))
            return fid
        self._facts[fid] = Fact(
            fact_id=fid, kind=kind,
            subject=Subject(kind=subject_kind or kind.rsplit(".", 1)[0], key=subject, display=display or subject),
            value=value, unit=unit, status=status, override_exposure=exposure, scope=scope, source=source,
            default_ref=default_ref, overridden_by=overridden_by, unresolved_reason=unresolved_reason,
            extractor=self.extractor, inputs=inputs or [], exposure_sources=exposure_sources or [],
            link_ids=sorted(set(link_ids or [])), precedence=precedence, absent=absent,
        )
        return fid

    def get(self, fid: str) -> Fact:
        return self._facts[fid]

    def update(self, fid: str, **changes: Any) -> None:
        self._facts[fid] = self._facts[fid].model_copy(update=changes)

    def facts(self) -> list[Fact]:
        return sorted(self._facts.values(), key=lambda f: (f.kind, f.subject.key, sorted(f.scope.items()), f.fact_id))
