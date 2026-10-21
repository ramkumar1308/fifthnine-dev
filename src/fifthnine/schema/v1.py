"""FifthNine Scan result, fact and finding schema, version 1.

Frozen at M0, revision 2 (D2, D3, D9; see docs/fifthnine/m0/SCHEMA_CHANGES_R2.md). Changes that remove or rename fields, or change the meaning of an enum value,
require a new schema version and CEO approval (contract section 14). Additive optional fields
are allowed within v1 and must be listed in docs/fifthnine/m0/SCHEMAS.md.

The JSON output of `fifthnine scan --format json` validates against schemas/result.v1.json,
which is generated from these models (scripts/export_schemas.py).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_ID: Final = "fifthnine.result/v1"
DISCLAIMER = (
    "FifthNine reports evidence about specific, documented reliability checks on the files it could read. "
    "It does not certify production readiness and does not prevent outages. A scan with no findings is not a "
    "statement that the system is production ready."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- enums


class Verdict(str, Enum):
    """Outcome of one rule for one subject. Exactly one per finding."""

    VIOLATION = "VIOLATION"  # check failed and every required input is established
    CONDITIONAL = "CONDITIONAL"  # check would fail, but at least one input is inferred or context-dependent
    UNKNOWN = "UNKNOWN"  # a required input could not be established; nothing is concluded
    SATISFIED = "SATISFIED"  # this specific check passed for this subject, within the stated scope and assumptions


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FactStatus(str, Enum):
    DECLARED = "declared"  # literal value in an active source
    DEFAULT = "default"  # framework default for a resolved version, cited
    OVERRIDDEN = "overridden"  # declared but superseded by a higher-precedence source
    INFERRED = "inferred"  # derived by heuristic; never supports a VIOLATION on its own
    UNRESOLVED = "unresolved"  # placeholder, out-of-repo source, unknown profile or version


class OverrideExposure(str, Enum):
    """Whether something outside the repository could replace this value at runtime."""

    NONE = "none"  # no known mechanism (code literal, manifest field, CLI flag with documented precedence)
    ENV_POSSIBLE = "env_possible"  # env-bindable and an out-of-repo env source is attached or context unknown
    FILE_POSSIBLE = "file_possible"  # out-of-repo file mounted where the framework reads config


class LinkStatus(str, Enum):
    DECLARED = "declared"  # written in .fifthnine.yml and its evidence digest still matches
    INFERRED = "inferred"  # proposed by FifthNine from evidence; advisory only (E4)
    STALE = "stale"  # declared, but the evidence it was accepted on has changed; treated as inferred (D3)
    UNRESOLVED = "unresolved"  # no unambiguous proposal


class AssumptionKind(str, Enum):
    EXTERNAL_OVERRIDE_ENV = "external_override_env"  # out-of-repo env source may override a value
    EXTERNAL_OVERRIDE_FILE = "external_override_file"  # out-of-repo file mounted where the framework reads config
    LINKAGE_INFERRED = "linkage_inferred"  # app-to-workload link is inferred or stale
    CLASSPATH_UNRESOLVED = "classpath_unresolved"  # transitive dependencies not resolved offline
    CLUSTER_ADMISSION_DEFAULT = "cluster_admission_default"  # e.g. LimitRange default requests injected at admission
    PLATFORM_BEHAVIOR = "platform_behavior"  # e.g. GitOps tool configured to ignore a field
    OUT_OF_REPO_ROUTING = "out_of_repo_routing"  # Ingress/Gateway objects outside the repository
    UNTESTED_VERSION = "untested_version"  # detected version not in the tested support matrix (D9)


class AssumptionState(str, Enum):
    OPEN = "open"  # FifthNine assumed it; nobody has declared it
    DECLARED_BY_TEAM = "declared_by_team"  # stated in .fifthnine.yml; recorded with its location


class SupportLevel(str, Enum):
    TESTED = "tested"  # version listed as tested in src/fifthnine/data/support_matrix.v1.yaml
    UNTESTED = "untested"  # inside the approved range (E5) but not tested, or version not determined
    UNSUPPORTED = "unsupported"  # outside the approved range; rules relying on it are not evaluated


class BaselineStatus(str, Enum):
    NEW = "new"
    EXISTING = "existing"
    SUPPRESSED = "suppressed"
    SUPPRESSION_EXPIRED = "suppression_expired"  # reported and re-activated, never silently ignored


class ScanStatus(str, Enum):
    FAIL_VERIFIED = "FAIL_VERIFIED"  # at least one unsuppressed VIOLATION
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # no VIOLATION, at least one CONDITIONAL
    INCOMPLETE = "INCOMPLETE"  # no VIOLATION or CONDITIONAL, but UNKNOWNs or blocking coverage gaps, or nothing assessable
    PASS_WITH_COVERAGE = "PASS_WITH_COVERAGE"  # noqa: S105 - status name, not a secret. Only SATISFIED results; read with coverage


class Domain(str, Enum):
    RESILIENCE = "resilience"
    CAPACITY = "capacity"
    DEPLOYMENT_SAFETY = "deployment_safety"
    PRODUCTION_CONFIG = "production_config"
    NETWORK_EXPOSURE = "network_exposure"
    DATA_COMPATIBILITY = "data_compatibility"  # reserved; no v0.1 rules (E2)
    FAILURE_CONTAINMENT = "failure_containment"


# ---------------------------------------------------------------- shared parts


class SourceRef(_Model):
    path: str = Field(description="Repository-relative POSIX path. Never absolute.")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    column: int | None = Field(default=None, ge=1)
    excerpt: str | None = Field(
        default=None, description="Short excerpt with secret-like values redacted as '****'. Optional."
    )


class Reference(_Model):
    title: str
    url: str | None = None
    source_path: str | None = Field(default=None, description="Pinned upstream source file, e.g. tag + path.")


class DefaultRef(_Model):
    """Citation for a value that comes from a framework default rather than the repository."""

    framework: str
    version_range: str
    reference: Reference


class Subject(_Model):
    kind: str = Field(description="e.g. k8s.container, http_client, spring.application, retry_policy")
    key: str = Field(description="Stable identity used in fingerprints, e.g. 'k8s:Deployment/commerce/orders-service/orders-service'")
    display: str


class Assumption(_Model):
    kind: AssumptionKind
    statement: str = Field(description="What must be true for the stated computation to hold")
    material: bool = Field(description="True if the assumption being false could change the verdict")
    state: AssumptionState = AssumptionState.OPEN
    fact_ids: list[str] = Field(default_factory=list)
    declared_in: SourceRef | None = Field(default=None, description="Required when state is declared_by_team")

    @model_validator(mode="after")
    def _declared_has_location(self) -> Assumption:
        if self.state is AssumptionState.DECLARED_BY_TEAM and self.declared_in is None:
            raise ValueError("declared_by_team assumptions must cite where the team declared them")
        return self


class ComponentSupport(_Model):
    component: str = Field(description="e.g. spring-boot, spring-framework, tenacity, kubernetes-api")
    version: str | None = Field(description="Detected version; null when it could not be determined")
    version_source: SourceRef | None = None
    support: SupportLevel
    tested_versions: list[str] = Field(default_factory=list, description="Tested versions from the support matrix for this component")
    matrix_entry: str | None = Field(default=None, description="Entry id in src/fifthnine/data/support_matrix.v1.yaml")


# ---------------------------------------------------------------- facts and links


class Fact(_Model):
    fact_id: str
    kind: str = Field(description="Namespaced fact kind, e.g. spring.property, k8s.probe.readiness, http_client.read_timeout")
    subject: Subject
    value: Any = None
    unit: str | None = None
    status: FactStatus
    override_exposure: OverrideExposure = OverrideExposure.NONE
    scope: dict[str, str] = Field(default_factory=dict, description="e.g. {'profile': 'prod'}")
    source: SourceRef | None = None
    default_ref: DefaultRef | None = None
    overridden_by: str | None = Field(default=None, description="fact_id of the winning fact")
    unresolved_reason: str | None = None
    extractor: str = Field(description="extractor id and version, e.g. spring-config-v1")


class Linkage(_Model):
    link_id: str
    kind: Literal["workload_container_to_image", "image_to_source_root", "workload_container_to_source_root"]
    status: LinkStatus
    from_ref: str
    to_ref: str
    evidence: list[str] = Field(default_factory=list)
    accept_command: str | None = Field(default=None, description="Shown when status is inferred or stale")
    declared_in: SourceRef | None = Field(default=None, description="Location in .fifthnine.yml when declared or stale")
    accepted_by: Literal["user", "links-accept"] | None = None
    accepted_on: str | None = Field(default=None, description="ISO date recorded at acceptance")
    evidence_digest: str | None = Field(default=None, description="Digest recorded at acceptance")
    current_digest: str | None = Field(default=None, description="Digest of the same evidence in this scan; differs when stale")

    @model_validator(mode="after")
    def _provenance(self) -> Linkage:
        if self.status in (LinkStatus.DECLARED, LinkStatus.STALE) and self.declared_in is None:
            raise ValueError("declared and stale linkages must cite their .fifthnine.yml location")
        if self.accepted_by == "links-accept" and (self.evidence_digest is None or self.accepted_on is None):
            raise ValueError("links-accept linkages need evidence_digest and accepted_on")
        if self.status is LinkStatus.DECLARED and self.evidence_digest and self.current_digest and self.evidence_digest != self.current_digest:
            raise ValueError("a linkage whose evidence digest changed must be reported as stale")
        if self.status is LinkStatus.STALE and self.evidence_digest == self.current_digest:
            raise ValueError("stale requires a changed evidence digest")
        return self


# ---------------------------------------------------------------- findings


class Remediation(_Model):
    summary: str
    references: list[Reference] = Field(default_factory=list)


class Suppression(_Model):
    reason: str
    expires: str = Field(description="ISO date. Required.")
    source: SourceRef


class Finding(_Model):
    fingerprint: str = Field(description="sha256 over rule_id + subject.key. Survives line moves and value edits.")
    rule_id: str
    rule_version: str
    domain: Domain
    verdict: Verdict
    severity: Severity | None = Field(default=None, description="Required for VIOLATION and CONDITIONAL")
    title: str
    message: str
    subject: Subject
    locations: list[SourceRef] = Field(description="Primary location first")
    fact_ids: list[str] = Field(default_factory=list)
    requirement: str = Field(description="The documented semantics being checked")
    computation: str | None = Field(default=None, description="Repository-derived calculation; kept when the verdict is CONDITIONAL (D2)")
    assumptions: list[Assumption] = Field(default_factory=list)
    verdict_if_assumptions_hold: Verdict | None = Field(
        default=None, description="For CONDITIONAL: the verdict the repository evidence gives if every open assumption holds"
    )
    conditional_reasons: list[str] = Field(default_factory=list, description="Human-readable; mirrors open material assumptions")
    unknown_reason: str | None = None
    applicability: list[ComponentSupport] = Field(default_factory=list)
    remediation: Remediation | None = None
    references: list[Reference] = Field(default_factory=list)
    blocking_eligible: bool = False
    blocking_ineligible_reasons: list[str] = Field(default_factory=list)
    baseline_status: BaselineStatus = BaselineStatus.NEW
    suppression: Suppression | None = None

    @model_validator(mode="after")
    def _verdict_invariants(self) -> Finding:
        open_material = [a for a in self.assumptions if a.material and a.state is AssumptionState.OPEN]
        if self.verdict is Verdict.VIOLATION:
            if open_material:
                raise ValueError("VIOLATION cannot rest on open material assumptions (D2); use CONDITIONAL or UNKNOWN")
            if any(c.support is not SupportLevel.TESTED for c in self.applicability):
                raise ValueError("VIOLATION requires every applicable component version to be tested (D9)")
        if self.verdict in (Verdict.VIOLATION, Verdict.CONDITIONAL) and self.severity is None:
            raise ValueError("severity is required for VIOLATION and CONDITIONAL")
        if self.verdict is Verdict.CONDITIONAL:
            if not open_material:
                raise ValueError("CONDITIONAL needs at least one open material assumption naming the uncertainty")
            if self.verdict_if_assumptions_hold is None:
                raise ValueError("CONDITIONAL needs verdict_if_assumptions_hold")
        elif self.verdict_if_assumptions_hold is not None:
            raise ValueError("verdict_if_assumptions_hold is only set for CONDITIONAL")
        if self.verdict is Verdict.UNKNOWN and not self.unknown_reason:
            raise ValueError("UNKNOWN requires unknown_reason")
        if any(c.support is SupportLevel.UNSUPPORTED for c in self.applicability):
            raise ValueError("rules are not evaluated on unsupported versions; emit a COV-UNSUPPORTED notice instead")
        if self.blocking_eligible and self.verdict is not Verdict.VIOLATION:
            raise ValueError("only VIOLATION findings can be blocking-eligible")
        return self


class CoverageNotice(_Model):
    code: str = Field(description="FN-OPS-001 (domain not assessable), FN-OPS-002 (unresolved input), COV-DELEGATED, COV-UNSUPPORTED")
    domain: Domain | None = None
    scope: str = Field(description="Path, object or 'repository'")
    message: str
    affects_rules: list[str] = Field(default_factory=list)
    blocks_assessment: bool = False
    delegated_to: list[str] = Field(default_factory=list, description="For COV-DELEGATED, e.g. ['ruff:S113', 'bandit:B113']")


# ---------------------------------------------------------------- result


class ToolInfo(_Model):
    name: Literal["fifthnine"] = "fifthnine"
    version: str
    rule_pack_versions: dict[str, str] = Field(default_factory=dict)


class ScanInfo(_Model):
    root: Literal["."] = "."
    mode: Literal["full", "diff"]
    base_ref: str | None = None
    profile: str | None = None
    profile_source: str | None = Field(default=None, description="cli | config | k8s-env:<path>:<line> | none")
    config_file: str | None = None
    policy_mode: Literal["report", "gate"] = "report"
    fail_on: Severity = Severity.HIGH
    network_used: Literal[False] = False
    code_executed: Literal[False] = False


class SkippedFile(_Model):
    path: str
    reason: Literal["ignored", "too_large", "symlink_outside_root", "unsupported", "parse_error", "binary"]
    detail: str | None = None


class Inventory(_Model):
    files_considered: int
    files_parsed: int
    skipped: list[SkippedFile] = Field(default_factory=list)
    detected_stacks: list[ComponentSupport] = Field(default_factory=list)


class AssessmentLimits(_Model):
    """Per-scan counters for CEO condition 4: how often missing linkage or unsupported config limits the result."""

    linkages_declared: int = 0
    linkages_inferred: int = 0
    linkages_unresolved: int = 0
    findings_capped_by_linkage: int = Field(0, description="Would be VIOLATION with declared linkage")
    findings_with_override_assumption: int = Field(0, description="Findings carrying an open external_override_* assumption")
    findings_conditional_on_external_config: int = Field(0, description="Would be VIOLATION if external config assumptions held (D2)")
    findings_conditional_on_untested_version: int = Field(0, description="CONDITIONAL because a detected version is untested (D9)")
    results_unknown_unsupported_config: int = Field(0, description="UNKNOWN because an input is unsupported (Helm, config import, ...)")
    results_unknown_unresolved_value: int = Field(0, description="UNKNOWN because a value is a placeholder or out of repo")
    rules_not_assessable: int = Field(0, description="Applicable rules that produced no verdict for any subject")


class Summary(_Model):
    by_verdict: dict[Verdict, int]
    by_severity: dict[Severity, int]
    blocking: int
    new: int = 0
    suppressed: int = 0
    rules_evaluated: int
    rules_not_applicable: int
    assessment_limits: AssessmentLimits = Field(default_factory=AssessmentLimits)


class AdvisorySection(_Model):
    """Reserved for a future, opt-in AI advisory mode. Never mixed into findings, never blocking.

    v0.1 always emits {"enabled": false, "items": []}.
    """

    enabled: Literal[False] = False
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=0)


class ScanResult(_Model):
    schema_id: Literal["fifthnine.result/v1"] = Field(default=SCHEMA_ID, alias="schema")
    simulated: bool = Field(default=False, description="True only for M0 mock output. Real scans never set it.")
    tool: ToolInfo
    scan: ScanInfo
    status: ScanStatus
    status_reason: str
    disclaimer: str = DISCLAIMER
    inventory: Inventory
    linkages: list[Linkage] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[CoverageNotice] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list, description="Facts referenced by findings (default) or all (--facts all)")
    summary: Summary
    advisory: AdvisorySection = Field(default_factory=AdvisorySection)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# ---------------------------------------------------------------- derivations (normative, used by the engine)


def derive_status(findings: list[Finding], coverage: list[CoverageNotice], rules_evaluated: int) -> ScanStatus:
    active = [f for f in findings if f.baseline_status is not BaselineStatus.SUPPRESSED]
    verdicts = {f.verdict for f in active}
    if Verdict.VIOLATION in verdicts:
        return ScanStatus.FAIL_VERIFIED
    if Verdict.CONDITIONAL in verdicts:
        return ScanStatus.REVIEW_REQUIRED
    if Verdict.UNKNOWN in verdicts or any(c.blocks_assessment for c in coverage) or rules_evaluated == 0:
        return ScanStatus.INCOMPLETE
    return ScanStatus.PASS_WITH_COVERAGE


def derive_verdict(
    check: Literal["fail", "pass", "indeterminate"],
    material_facts: list[Fact],
    linkages: list[Linkage],
    applicability: list[ComponentSupport],
    team_declarations: dict[AssumptionKind, SourceRef] | None = None,
    rule_assumptions: list[Assumption] | None = None,
) -> tuple[Verdict, list[Assumption], Verdict | None]:
    """Normative verdict logic (D2, D9). Rules compute `check` from repository facts; this function decides
    how much of that result can be stated. Returns (verdict, assumptions, verdict_if_assumptions_hold).

    - A material value that does not exist (unresolved fact, unresolved linkage) or an indeterminate check: UNKNOWN.
    - A material value that exists but could be replaced from outside the repository, an inferred or stale
      linkage, or an untested version: the computation still runs, the assumption is recorded, and a failing
      check is CONDITIONAL with verdict_if_assumptions_hold = VIOLATION.
    - Rules add context assumptions the facts cannot express (classpath, admission defaults, GitOps behaviour,
      out-of-repo routing) through rule_assumptions.
    - A team declaration in .fifthnine.yml turns the matching assumption into declared_by_team.
    - Unsupported versions must be filtered out before calling (COV-UNSUPPORTED).
    """
    declared = team_declarations or {}
    if any(c.support is SupportLevel.UNSUPPORTED for c in applicability):
        raise ValueError("do not evaluate rules on unsupported versions")
    if check == "indeterminate" or any(f.status is FactStatus.UNRESOLVED for f in material_facts) or any(
        link.status is LinkStatus.UNRESOLVED for link in linkages
    ):
        return Verdict.UNKNOWN, [], None

    assumptions: list[Assumption] = []

    def add(kind: AssumptionKind, statement: str, fact_ids: list[str]) -> None:
        where = declared.get(kind)
        assumptions.append(Assumption(kind=kind, statement=statement, material=True, fact_ids=fact_ids,
                                      state=AssumptionState.DECLARED_BY_TEAM if where else AssumptionState.OPEN,
                                      declared_in=where))

    env = [f.fact_id for f in material_facts if f.override_exposure is OverrideExposure.ENV_POSSIBLE]
    if env:
        add(AssumptionKind.EXTERNAL_OVERRIDE_ENV, "out-of-repo environment sources do not override these values", env)
    files = [f.fact_id for f in material_facts if f.override_exposure is OverrideExposure.FILE_POSSIBLE]
    if files:
        add(AssumptionKind.EXTERNAL_OVERRIDE_FILE, "out-of-repo config files do not override these values", files)
    inferred_facts = [f.fact_id for f in material_facts if f.status is FactStatus.INFERRED]
    weak_links = [link.link_id for link in linkages if link.status in (LinkStatus.INFERRED, LinkStatus.STALE)]
    if weak_links or inferred_facts:
        add(AssumptionKind.LINKAGE_INFERRED, f"linkage {', '.join(weak_links) or 'inference'} is correct", inferred_facts)
    untested = [f"{c.component} {c.version or '(unknown version)'}" for c in applicability if c.support is SupportLevel.UNTESTED]
    if untested:
        add(AssumptionKind.UNTESTED_VERSION, "semantics of " + ", ".join(untested) + " match the tested versions", [])

    for extra in rule_assumptions or []:
        where = declared.get(extra.kind)
        assumptions.append(extra.model_copy(update={"state": AssumptionState.DECLARED_BY_TEAM, "declared_in": where}) if where else extra)

    open_material = any(a.material and a.state is AssumptionState.OPEN for a in assumptions)
    if check == "fail":
        return (Verdict.CONDITIONAL, assumptions, Verdict.VIOLATION) if open_material else (Verdict.VIOLATION, assumptions, None)
    return Verdict.SATISFIED, assumptions, None


def blocking_ineligible_reasons(verdict: Verdict, rule_blocking_capable: bool, linkages: list[Linkage], suppressed: bool) -> list[str]:
    """Empty list means the finding may block CI in gate mode at or above fail_on. The verdict invariants on
    Finding already exclude open material assumptions and untested versions from VIOLATION."""
    reasons: list[str] = []
    if verdict is not Verdict.VIOLATION:
        reasons.append(f"verdict is {verdict.value}")
    if not rule_blocking_capable:
        reasons.append("rule not yet promoted to blocking: needs runtime and boundary-case tests (D5)")
    for link in linkages:
        if link.status is not LinkStatus.DECLARED:
            reasons.append(f"linkage {link.link_id} is {link.status.value}")
    if suppressed:
        reasons.append("suppressed")
    return reasons
