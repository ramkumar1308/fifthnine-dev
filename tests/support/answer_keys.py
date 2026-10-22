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

# Closed precedence vocabulary (scored). Extend it in the same change that introduces a new resolution path.
PRECEDENCE = frozenset({
    "docker.entrypoint", "k8s.container.command", "gunicorn.cli",
    "spring.os_environment", "spring.packaged_application_file", "spring.packaged_profile_file:prod",
    "spring.placeholder:os_environment", "spring.actuator.path_algorithm", "spring.boot_builder_customizer",
})
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
    applied_version: str | None = Field(description="Component version the default was looked up for; null only for version-stable defaults verified under D3")
    repository: str
    tag: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str
    line: int = Field(ge=1)
    anchor: str
    verified_at: list[str] = Field(default_factory=list, description="Releases where the default was checked to be identical (D3)")
    note: str | None = None

    @model_validator(mode="after")
    def _version_or_verified(self) -> UpstreamDefault:
        if self.applied_version is None and (len(self.verified_at) < 2 or self.tag not in self.verified_at):
            raise ValueError("a default without an applied version must be verified at several supported releases, including the cited one (D3)")
        return self


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
        if self.precedence is not None and self.precedence not in PRECEDENCE:
            raise ValueError(f"{self.id}: precedence {self.precedence!r} is not in the vocabulary")
        if s is FactStatus.DEFAULT and self.kind != "stack.version" and not any(i.role is InputRole.ABSENCE for i in self.inputs):
            raise ValueError(f"{self.id}: a default that fills an unset field names where it is unset (absence input)")
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
    value_is_null: bool | None = None
    status_not: FactStatus | None = None
    input_fact: str | None = None
    input_role: InputRole | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Forbid:
        if not self.model_dump(exclude_none=True):
            raise ValueError("a negative key needs at least one predicate")
        if not (self.kind or self.kind_prefix or self.subject or self.subject_prefix or self.source_path or self.input_fact):
            raise ValueError("a negative key must narrow by kind, subject, source or input")
        return self


def violates(forbid: Forbid, fact: LabelledFact) -> bool:
    """True when `fact` matches every predicate in `forbid`. Semantics (also used by the M2 scorer):
    scope is a subset match; value is equality; value_not is inequality; value_contains is a substring match on a
    string value, or on any element of a list value; input_fact matches an input of input_role (any role if unset)."""
    if forbid.kind is not None and fact.kind != forbid.kind:
        return False
    if forbid.kind_prefix is not None and not fact.kind.startswith(forbid.kind_prefix):
        return False
    if forbid.subject is not None and fact.subject != forbid.subject:
        return False
    if forbid.subject_prefix is not None and not fact.subject.startswith(forbid.subject_prefix):
        return False
    if forbid.scope is not None and any(fact.scope.get(k) != v for k, v in forbid.scope.items()):
        return False
    if forbid.source_path is not None and (fact.source is None or fact.source.path != forbid.source_path):
        return False
    if forbid.value is not None and fact.value != forbid.value:
        return False
    if forbid.value_not is not None and fact.value == forbid.value_not:
        return False
    if forbid.value_contains is not None:
        v = fact.value
        items = v if isinstance(v, list) else [v]
        if not any(isinstance(i, str) and forbid.value_contains in i for i in items):
            return False
    if forbid.value_is_null is not None and (fact.value is None) != forbid.value_is_null:
        return False
    if forbid.status_not is not None and fact.status is forbid.status_not:
        return False
    if forbid.input_fact is not None and not any(
        i.fact == forbid.input_fact and (forbid.input_role is None or i.role is forbid.input_role) for i in fact.inputs
    ):
        return False
    return True


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
            if f.overridden_by and f.id not in by_id[f.overridden_by].supersedes:
                raise ValueError(f"{f.id} names {f.overridden_by} as winner, which does not list it in supersedes")
            sources = [by_id[i.fact] for i in f.inputs if i.fact and i.role is InputRole.VALUE_SOURCE]
            if f.status is FactStatus.DECLARED and any(x.status is FactStatus.UNRESOLVED for x in sources):
                raise ValueError(f"{f.id} is declared but derived from an unresolved fact")
            if f.override_exposure is OverrideExposure.NONE and any(x.override_exposure is not OverrideExposure.NONE for x in sources):
                raise ValueError(f"{f.id} derives from an exposed value but has override_exposure none")
            lost = sorted({lk for x in sources for lk in x.link_ids} - set(f.link_ids))
            if lost:
                raise ValueError(f"{f.id} derives from facts that depend on {lost} but does not carry them")
            if f.default_ref and f.default_ref.applied_version is not None:
                basis = [by_id[i.fact] for i in f.inputs if i.fact and i.role is InputRole.VERSION_BASIS]
                if not any(b.value == f.default_ref.applied_version and b.subject == f"component:{f.default_ref.framework}" for b in basis):
                    raise ValueError(f"{f.id}: applied_version {f.default_ref.applied_version} is not the value of a {f.default_ref.framework} version input")
        for n in self.negative:
            if n.forbid.input_fact and n.forbid.input_fact not in by_id:
                raise ValueError(f"{n.id} references unknown fact {n.forbid.input_fact}")
            if n.forbid.input_fact and MILESTONE_ORDER[by_id[n.forbid.input_fact].milestone] > MILESTONE_ORDER[n.milestone]:
                raise ValueError(f"{n.id} is checked before the fact it references exists")
            hits = [f.id for f in self.facts if violates(n.forbid, f)]
            if hits:
                raise ValueError(f"{n.id} is violated by the key's own facts {hits}")
        return self


class Licence(_Model):
    spdx: str
    file: str
    note: str | None = None


class CorpusEntry(_Model):
    id: str = Field(pattern=r"^(SB|FA)-\d{2}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    cohort: Literal["generalization", "educational"]
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
