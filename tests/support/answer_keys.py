"""Models for development answer keys (M2-0).

Two kinds of file live under benchmarks/dev:

- corpus/corpus.v1.yaml: the public development corpus (A14, M0-D8), pinned by commit.
- facts/<sample>.facts.yaml: hand-labelled facts for the in-repository samples. M2 extractors are scored
  against these keys (M2_PLAN.md acceptance criterion 3).

Keys are development data. They are never held-out benchmark cases and must never be reported as such.
The field names mirror the Fact model in fifthnine.schema.v1 plus the additive fields proposed in
docs/fifthnine/M2-0.md (schema revision 3 proposal). If that proposal changes, these models change with it.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fifthnine.core.safeparse import load_yaml_documents, to_plain
from fifthnine.schema.v1 import FactStatus, OverrideExposure


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Loc(_Model):
    """A repository-relative location plus text that must appear on that line (checked by the key validator)."""

    path: str
    line: int = Field(ge=1)
    anchor: str = Field(min_length=1)


class InputRole(str, Enum):
    VALUE_SOURCE = "value_source"  # where the winning literal is written
    PLACEHOLDER = "placeholder"  # ${VAR:default} expression that was resolved
    ENV_DEFINITION = "env_definition"  # where an environment variable gets its value
    ENV_ATTACHMENT = "env_attachment"  # env / envFrom entry that makes a variable visible to the container
    PROFILE_ACTIVATION = "profile_activation"
    PROPERTY_REFERENCE = "property_reference"  # Maven ${prop} use site, Gradle catalog reference
    VERSION_BASIS = "version_basis"  # the resolved component version a default lookup used
    REFERENCE = "reference"  # selector, backend, scaleTargetRef, named port, include_router, decorator binding
    ABSENCE = "absence"  # the object where a field is not set, so a default applies
    SUPERSEDED = "superseded"  # lower-precedence value that lost


class Input(_Model):
    role: InputRole
    loc: Loc | None = None
    fact: str | None = Field(default=None, description="id of another labelled fact")

    @model_validator(mode="after")
    def _one_target(self) -> Input:
        if (self.loc is None) == (self.fact is None):
            raise ValueError("an input names exactly one of loc or fact")
        return self


class UpstreamDefault(_Model):
    """Framework-version semantics: the cited default at a pinned upstream tag."""

    framework: str
    applied_version: str | None = Field(description="Component version the default was looked up for; null only for documented version-independent defaults")
    repository: str
    tag: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str
    line: int = Field(ge=1)
    anchor: str
    note: str | None = None


class LabelledFact(_Model):
    id: str = Field(pattern=r"^[A-Z]{2}-[A-Z]{1,3}\d{2}$")
    kind: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    subject: str
    scope: dict[str, str] = Field(default_factory=dict)
    value: Any = None
    unit: str | None = None
    status: FactStatus
    override_exposure: OverrideExposure = OverrideExposure.NONE
    exposure_sources: list[str] = Field(default_factory=list)
    source: Loc | None = None
    inputs: list[Input] = Field(default_factory=list)
    precedence: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    overridden_by: str | None = None
    link: str | None = None
    default: UpstreamDefault | None = None
    unresolved_reason: str | None = None
    milestone: Literal["M2-1", "M2-2", "M2-3", "M2-4", "M2-5"]
    note: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> LabelledFact:
        if self.status is FactStatus.DEFAULT and self.default is None:
            raise ValueError(f"{self.id}: a default fact must cite the upstream default")
        if self.status is not FactStatus.DEFAULT and self.default is not None:
            raise ValueError(f"{self.id}: only default facts carry an upstream citation")
        if self.status is FactStatus.UNRESOLVED and not self.unresolved_reason:
            raise ValueError(f"{self.id}: an unresolved fact needs a reason")
        if self.status is FactStatus.OVERRIDDEN and not self.overridden_by:
            raise ValueError(f"{self.id}: an overridden fact names the fact that won")
        if self.status in (FactStatus.DECLARED, FactStatus.OVERRIDDEN, FactStatus.INFERRED) and self.source is None:
            raise ValueError(f"{self.id}: a {self.status.value} fact needs a source location")
        if self.override_exposure is not OverrideExposure.NONE and not self.exposure_sources:
            raise ValueError(f"{self.id}: name the mechanism behind override_exposure")
        return self


class NegativeKey(_Model):
    """Something the extractor must not produce (traps such as RULES.md F3 and F13)."""

    id: str = Field(pattern=r"^[A-Z]{2}-N\d{2}$")
    statement: str
    forbid: dict[str, Any]
    milestone: Literal["M2-1", "M2-2", "M2-3", "M2-4", "M2-5"]


class LabelledLink(_Model):
    id: str = Field(pattern=r"^[A-Z]{2}-L\d{2}$")
    kind: Literal["workload_container_to_image", "image_to_source_root", "workload_container_to_source_root"]
    status: Literal["declared", "inferred", "stale", "unresolved"]
    from_ref: str
    to_ref: str
    evidence: list[Loc]
    milestone: Literal["M2-5"]


class FactKey(_Model):
    schema_: Literal["fifthnine.devkey.facts/v1"] = Field(alias="schema")
    sample: str
    mode: Literal["default"]
    mode_note: str
    labelled_by: str
    reviewed_by: str | None
    links: list[LabelledLink]
    facts: list[LabelledFact]
    negative: list[NegativeKey]

    @model_validator(mode="after")
    def _references(self) -> FactKey:
        ids = [f.id for f in self.facts] + [n.id for n in self.negative] + [lk.id for lk in self.links]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate ids: {dupes}")
        fact_ids = {f.id for f in self.facts}
        link_ids = {lk.id for lk in self.links}
        for f in self.facts:
            refs = [i.fact for i in f.inputs if i.fact] + f.supersedes + ([f.overridden_by] if f.overridden_by else [])
            missing = [r for r in refs if r not in fact_ids]
            if missing:
                raise ValueError(f"{f.id} references unknown facts {missing}")
            if f.link and f.link not in link_ids:
                raise ValueError(f"{f.id} references unknown link {f.link}")
        by_id = {f.id: f for f in self.facts}
        for f in self.facts:
            for loser in f.supersedes:
                if by_id[loser].overridden_by not in (f.id, None):
                    raise ValueError(f"{f.id} supersedes {loser}, which names a different winner")
        return self


class Licence(_Model):
    spdx: str
    file: str
    note: str | None = None


class CorpusEntry(_Model):
    id: str = Field(pattern=r"^(SB|FA)-\d{2}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    commit_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    framework: Literal["spring-boot", "fastapi"]
    licence: Licence
    deployment_shape: Literal["plain", "plain+kustomize", "plain+helm", "helm-only"]
    linkage_expectation: Literal["in-repo-app", "partial", "none"]
    rationale: str
    m1_baseline: str

    @field_validator("rationale")
    @classmethod
    def _rationale_specific(cls, v: str) -> str:
        if len(v) < 40:
            raise ValueError("rationale must say why this repository is in the corpus")
        return v


class Corpus(_Model):
    schema_: Literal["fifthnine.devcorpus/v1"] = Field(alias="schema")
    status: Literal["proposed", "approved", "frozen"]
    selected_on: str
    selection_doc: str
    entries: list[CorpusEntry]

    @model_validator(mode="after")
    def _unique(self) -> Corpus:
        for attr in ("id", "repository"):
            values = [getattr(e, attr) for e in self.entries]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {attr} in corpus")
        return self


def load(path: Path, model: type[BaseModel]) -> Any:
    docs = load_yaml_documents(path.read_text(encoding="utf-8"))
    return model.model_validate(to_plain(docs[0].data))
