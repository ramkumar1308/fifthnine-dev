"""Models for development answer keys (M2-0).

Two kinds of file live under benchmarks/dev:

- corpus/corpus.v1.yaml: the public development corpus (A14, M0-D8), pinned by commit.
- facts/<sample>.facts.yaml: hand-labelled facts for the in-repository samples. M2 extractors are scored
  against these keys (M2_PLAN.md acceptance criterion 3; scoring rules in docs/fifthnine/M2-0.md).

Keys are development data. They are never held-out benchmark cases and must never be reported as such.

Field names follow fifthnine.schema.v1.Fact plus the revision 3 fields proposed in docs/fifthnine/M2-0.md, with
two documented simplifications: `source` and input locations are a `Loc` (one line plus anchor text) where the
output uses SourceRef(line_start=line_end=line), and `default_ref` carries the upstream citation directly.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fifthnine.core.safeparse import load_yaml_documents, to_plain
from fifthnine.schema.v1 import FactStatus, OverrideExposure

Milestone = Literal["M2-1", "M2-2", "M2-3", "M2-4", "M2-5"]
MILESTONE_ORDER = {"M2-1": 1, "M2-2": 2, "M2-3": 3, "M2-4": 4, "M2-5": 5}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Loc(_Model):
    """A repository-relative location plus text that must appear on that line (checked by the key validator)."""

    path: str
    line: int = Field(ge=1)
    anchor: str = Field(min_length=6)


class InputRole(str, Enum):
    VALUE_SOURCE = "value_source"  # where a contributing literal is written, or a fact whose value is used
    PLACEHOLDER = "placeholder"  # ${VAR:default} expression that was resolved
    ENV_ATTACHMENT = "env_attachment"  # env / envFrom entry that makes a variable visible to the container
    PROFILE_ACTIVATION = "profile_activation"
    PROPERTY_REFERENCE = "property_reference"  # Maven ${prop} use site
    VERSION_BASIS = "version_basis"  # the resolved component version a default lookup used
    REFERENCE = "reference"  # selector, backend, scaleTargetRef, named port, include_router, decorator binding, call
    ABSENCE = "absence"  # identity line of the object where a field is not set


class Input(_Model):
    role: InputRole
    loc: Loc | None = None
    fact: str | None = Field(default=None, description="id of another labelled fact")

    @model_validator(mode="after")
    def _one_target(self) -> Input:
        if (self.loc is None) == (self.fact is None):
            raise ValueError("an input names exactly one of loc or fact")
        return self


class ExposureSource(_Model):
    """The mechanism behind override_exposure (proposed Fact.exposure_sources)."""

    via: Literal["envFrom", "valueFrom", "volume", "inherited"]
    object: str | None = Field(default=None, description="Out-of-repository object, e.g. k8s:Secret/commerce/orders-service-secrets")
    variables: list[str] = Field(default_factory=list, description="Variables or keys that could carry the override; illustrative, not scored")
    from_facts: list[str] = Field(default_factory=list, description="For via=inherited: the facts whose exposure this one inherits")

    @model_validator(mode="after")
    def _shape(self) -> ExposureSource:
        if (self.via == "inherited") != bool(self.from_facts):
            raise ValueError("inherited exposure names from_facts, and only inherited exposure does")
        if self.via != "inherited" and not self.object:
            raise ValueError("a direct exposure source names the out-of-repository object")
        return self


class UpstreamDefault(_Model):
    """Framework-version semantics: the cited default at a pinned upstream tag (proposed DefaultRef.upstream)."""

    framework: str
    applied_version: str | None = Field(description="Component version the default was looked up for; null only under decision K1")
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
    absent: bool = Field(default=False, description="Proposed Fact.absent: the field is established as not set in the cited object")
    override_exposure: OverrideExposure = OverrideExposure.NONE
    exposure_sources: list[ExposureSource] = Field(default_factory=list)
    source: Loc | None = None
    inputs: list[Input] = Field(default_factory=list)
    precedence: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    overridden_by: str | None = None
    link_ids: list[str] = Field(default_factory=list)
    default_ref: UpstreamDefault | None = None
    unresolved_reason: str | None = None
    milestone: Milestone
    note: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> LabelledFact:
        s = self.status
        if (s is FactStatus.DEFAULT) != (self.default_ref is not None):
            raise ValueError(f"{self.id}: default facts, and only default facts, cite the upstream default")
        if s is FactStatus.UNRESOLVED and (not self.unresolved_reason or self.value is not None):
            raise ValueError(f"{self.id}: an unresolved fact has a null value and a reason")
        if s is not FactStatus.UNRESOLVED and self.unresolved_reason:
            raise ValueError(f"{self.id}: only unresolved facts carry unresolved_reason")
        if s is FactStatus.OVERRIDDEN and not self.overridden_by:
            raise ValueError(f"{self.id}: an overridden fact names the fact that won")
        if s in (FactStatus.DECLARED, FactStatus.OVERRIDDEN, FactStatus.INFERRED) and self.source is None:
            raise ValueError(f"{self.id}: a {s.value} fact needs a source location")
        if s is FactStatus.INFERRED and not self.note:
            raise ValueError(f"{self.id}: an inferred fact explains the heuristic (it adds a linkage_inferred assumption)")
        if self.absent and (s is not FactStatus.DECLARED or self.value is not None):
            raise ValueError(f"{self.id}: an absent fact is declared with a null value")
        if (self.override_exposure is OverrideExposure.NONE) == bool(self.exposure_sources):
            raise ValueError(f"{self.id}: exposure_sources are given exactly when override_exposure is not none")
        return self


class Forbid(_Model):
    """Closed predicate vocabulary for negative keys. All given fields must match for an extractor fact to violate it.
    Fact references (input_fact) name key fact ids; the scorer maps them to the matched extractor facts."""

    kind: str | None = None
    kind_prefix: str | None = None
    subject: str | None = None
    subject_prefix: str | None = None
    scope: dict[str, str] | None = None
    source_path: str | None = None
    value: Any = None
    value_not: Any = None
    value_contains: str | None = None
    status_not: FactStatus | None = None
    input_fact: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Forbid:
        if not self.model_dump(exclude_none=True):
            raise ValueError("a negative key needs at least one predicate")
        if not (self.kind or self.kind_prefix or self.subject or self.subject_prefix or self.source_path or self.input_fact):
            raise ValueError("a negative key must narrow by kind, subject, source or input")
        return self


class NegativeKey(_Model):
    """Something the extractor must not produce (traps such as RULES.md F3 and F13)."""

    id: str = Field(pattern=r"^[A-Z]{2}-N\d{2}$")
    statement: str
    forbid: Forbid
    milestone: Milestone


class LabelledLink(_Model):
    id: str = Field(pattern=r"^[A-Z]{2}-L\d{2}$")
    kind: Literal["workload_container_to_image", "image_to_source_root", "workload_container_to_source_root"]
    status: Literal["declared", "inferred", "stale", "unresolved"]
    from_ref: str
    to_ref: str
    evidence: list[Loc]
    milestone: Milestone


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
        by_id = {f.id: f for f in self.facts}
        links = {lk.id: lk for lk in self.links}
        for f in self.facts:
            refs = [i.fact for i in f.inputs if i.fact] + f.supersedes + ([f.overridden_by] if f.overridden_by else [])
            refs += [r for e in f.exposure_sources for r in e.from_facts]
            missing = [r for r in refs if r not in by_id] + [lk for lk in f.link_ids if lk not in links]
            if missing:
                raise ValueError(f"{f.id} references unknown ids {missing}")
            late = [r for r in refs if MILESTONE_ORDER[by_id[r].milestone] > MILESTONE_ORDER[f.milestone]]
            late += [lk for lk in f.link_ids if MILESTONE_ORDER[links[lk].milestone] > MILESTONE_ORDER[f.milestone]]
            if late:
                raise ValueError(f"{f.id} ({f.milestone}) depends on {late}, which land later")
            for i in f.inputs:
                if i.role is InputRole.VERSION_BASIS and (not i.fact or by_id[i.fact].kind != "stack.version"):
                    raise ValueError(f"{f.id}: version_basis must reference a stack.version fact")
        for f in self.facts:
            for loser in f.supersedes:
                if by_id[loser].overridden_by not in (f.id, None):
                    raise ValueError(f"{f.id} supersedes {loser}, which names a different winner")
        for n in self.negative:
            if n.forbid.input_fact and n.forbid.input_fact not in by_id:
                raise ValueError(f"{n.id} references unknown fact {n.forbid.input_fact}")
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
    in_supported_range: Literal["yes", "no", "unknown"]
    deployment_shape: Literal["plain", "plain+kustomize", "plain+helm", "helm-only"]
    linkage_expectation: Literal["in-repo-app", "partial", "none"]
    rationale: str = Field(min_length=40)
    m1_baseline: str


class Corpus(_Model):
    schema_: Literal["fifthnine.devcorpus/v1"] = Field(alias="schema")
    status: Literal["proposed", "approved", "frozen"]
    selected_on: str
    selection_doc: str
    entries: list[CorpusEntry]

    @model_validator(mode="after")
    def _unique(self) -> Corpus:
        for attr in ("id", "repository", "commit"):
            values = [getattr(e, attr) for e in self.entries]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {attr} in corpus")
        return self


def load(path: Path, model: type[BaseModel]) -> Any:
    docs = load_yaml_documents(path.read_text(encoding="utf-8"))
    if len(docs) != 1:
        raise ValueError(f"{path}: expected exactly one YAML document, found {len(docs)}")
    return model.model_validate(to_plain(docs[0].data))
