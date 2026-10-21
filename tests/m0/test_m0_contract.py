"""M0 contract checks (schema revision 2).

Simulated outputs validate against the frozen schema and cite real lines; status derivation never passes an
empty or unassessable scan; D2 (external overrides), D3 (linkage provenance) and D9 (tested versions) are
enforced by the models and by derive_verdict()."""

import glob
import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from pydantic import ValidationError

from fifthnine.schema.config_v1 import FifthNineConfig, ServiceDecl, WorkloadRef
from fifthnine.schema.support_v1 import SupportMatrix
from fifthnine.schema.v1 import (
    Assumption, AssumptionKind, AssumptionState, BaselineStatus, ComponentSupport, CoverageNotice, Domain, Fact,
    FactStatus, Finding, Linkage, LinkStatus, OverrideExposure, ScanResult, ScanStatus, Severity, SourceRef, Subject,
    SupportLevel, Verdict, derive_status, derive_verdict,
)

ROOT = Path(__file__).resolve().parents[2]
SIMULATED = sorted(glob.glob(str(ROOT / "docs/fifthnine/m0/simulated/*.json")))
LOC = SourceRef(path="a", line_start=1, line_end=1)
TESTED = ComponentSupport(component="spring-boot", version="3.4.13", support=SupportLevel.TESTED, tested_versions=["3.4.13"])
UNTESTED = ComponentSupport(component="spring-boot", version="3.4.12", support=SupportLevel.UNTESTED)
UNSUPPORTED = ComponentSupport(component="spring-boot", version="2.7.18", support=SupportLevel.UNSUPPORTED)


def finding(verdict: Verdict, **kw) -> Finding:
    base = dict(fingerprint="x", rule_id="FN-T-1", rule_version="0", domain=Domain.RESILIENCE, verdict=verdict,
                severity=None if verdict in (Verdict.SATISFIED, Verdict.UNKNOWN) else Severity.HIGH,
                title="t", message="m", subject=Subject(kind="k", key="k", display="d"), locations=[LOC], requirement="r")
    if verdict is Verdict.CONDITIONAL:
        base["assumptions"] = [Assumption(kind=AssumptionKind.EXTERNAL_OVERRIDE_ENV, statement="s", material=True)]
        base["verdict_if_assumptions_hold"] = Verdict.VIOLATION
    if verdict is Verdict.UNKNOWN:
        base["unknown_reason"] = "value is a placeholder"
    base.update(kw)
    return Finding(**base)


def fact(fid: str, status: FactStatus = FactStatus.DECLARED, exposure: OverrideExposure = OverrideExposure.NONE) -> Fact:
    return Fact(fact_id=fid, kind="k", subject=Subject(kind="k", key="k", display="d"), status=status,
                override_exposure=exposure, source=LOC, extractor="t")


def link(status: LinkStatus, **kw) -> Linkage:
    base = dict(link_id="L1", kind="workload_container_to_source_root", status=status, from_ref="a", to_ref="b")
    if status in (LinkStatus.DECLARED, LinkStatus.STALE):
        base.update(declared_in=LOC, accepted_by="links-accept", accepted_on="2026-10-21", evidence_digest="sha256:aa",
                    current_digest="sha256:aa" if status is LinkStatus.DECLARED else "sha256:bb")
    base.update(kw)
    return Linkage(**base)


# ---------------------------------------------------------------- simulated outputs


@pytest.mark.parametrize("path", SIMULATED)
def test_simulated_output_matches_frozen_schema(path: str) -> None:
    data = json.loads(Path(path).read_text())
    jsonschema.validate(data, json.loads((ROOT / "schemas/result.v1.json").read_text()))
    result = ScanResult.model_validate(data)  # also runs the D2/D3/D9 model validators
    assert result.simulated is True
    assert result.advisory.enabled is False and result.advisory.items == []


@pytest.mark.parametrize("path", SIMULATED)
def test_every_location_exists_in_sample(path: str) -> None:
    data = json.loads(Path(path).read_text())
    sample = ROOT / "samples" / Path(path).name.split(".")[0]
    for f in data["findings"]:
        for loc in f["locations"]:
            lines = (sample / loc["path"]).read_text().splitlines()
            assert 1 <= loc["line_start"] <= loc["line_end"] <= len(lines), (f["rule_id"], loc)


def test_simulated_counts_match_documented_table() -> None:
    expected = {"orders-service-spring.default": (2, 6, 3), "orders-service-spring.links-accepted": (2, 6, 3),
                "orders-service-spring.declared": (6, 2, 3), "orders-service-spring.today-untested": (0, 8, 3),
                "catalog-api-fastapi.default": (2, 3, 2)}
    for name, (v, c, s) in expected.items():
        by = json.loads((ROOT / f"docs/fifthnine/m0/simulated/{name}.json").read_text())["summary"]["by_verdict"]
        assert (by["VIOLATION"], by["CONDITIONAL"], by["SATISFIED"]) == (v, c, s), name


def test_no_rule_is_blocking_before_promotion() -> None:
    for path in SIMULATED:
        assert json.loads(Path(path).read_text())["summary"]["blocking"] == 0


# ---------------------------------------------------------------- status (C2)


def test_no_pass_without_evidence() -> None:
    assert derive_status([], [], rules_evaluated=0) is ScanStatus.INCOMPLETE
    assert derive_status([], [CoverageNotice(code="FN-OPS-001", scope="repo", message="m", blocks_assessment=True)], 3) is ScanStatus.INCOMPLETE
    assert derive_status([finding(Verdict.UNKNOWN)], [], 3) is ScanStatus.INCOMPLETE
    assert derive_status([finding(Verdict.CONDITIONAL)], [], 3) is ScanStatus.REVIEW_REQUIRED
    assert derive_status([finding(Verdict.SATISFIED)], [], 3) is ScanStatus.PASS_WITH_COVERAGE
    assert derive_status([finding(Verdict.VIOLATION, baseline_status=BaselineStatus.SUPPRESSED)], [], 3) is ScanStatus.PASS_WITH_COVERAGE
    assert derive_status([finding(Verdict.VIOLATION), finding(Verdict.SATISFIED)], [], 3) is ScanStatus.FAIL_VERIFIED


def test_status_enum_has_no_plain_pass() -> None:
    assert {s.value for s in ScanStatus} == {"FAIL_VERIFIED", "REVIEW_REQUIRED", "INCOMPLETE", "PASS_WITH_COVERAGE"}


# ---------------------------------------------------------------- D2: external overrides


def test_violation_rejects_open_external_override() -> None:
    with pytest.raises(ValidationError, match="D2"):
        finding(Verdict.VIOLATION, assumptions=[Assumption(kind=AssumptionKind.EXTERNAL_OVERRIDE_ENV, statement="s", material=True)])


def test_violation_allows_team_declared_assumption_with_location() -> None:
    a = Assumption(kind=AssumptionKind.EXTERNAL_OVERRIDE_ENV, statement="s", material=True,
                   state=AssumptionState.DECLARED_BY_TEAM, declared_in=SourceRef(path=".fifthnine.yml", line_start=23, line_end=24))
    assert finding(Verdict.VIOLATION, assumptions=[a]).verdict is Verdict.VIOLATION
    with pytest.raises(ValidationError):
        Assumption(kind=AssumptionKind.EXTERNAL_OVERRIDE_ENV, statement="s", material=True, state=AssumptionState.DECLARED_BY_TEAM)


def test_conditional_requires_named_assumption_and_preserved_result() -> None:
    with pytest.raises(ValidationError):
        finding(Verdict.CONDITIONAL, assumptions=[])
    with pytest.raises(ValidationError):
        finding(Verdict.CONDITIONAL, verdict_if_assumptions_hold=None)


def test_unknown_requires_reason() -> None:
    with pytest.raises(ValidationError):
        finding(Verdict.UNKNOWN, unknown_reason=None)


@pytest.mark.parametrize("exposure,kind", [(OverrideExposure.ENV_POSSIBLE, AssumptionKind.EXTERNAL_OVERRIDE_ENV),
                                           (OverrideExposure.FILE_POSSIBLE, AssumptionKind.EXTERNAL_OVERRIDE_FILE)])
def test_override_exposed_failure_is_conditional_with_computation_kept(exposure, kind) -> None:
    verdict, assumptions, if_hold = derive_verdict("fail", [fact("f1", exposure=exposure), fact("f2")], [], [TESTED])
    assert verdict is Verdict.CONDITIONAL and if_hold is Verdict.VIOLATION
    assert [a.kind for a in assumptions] == [kind] and assumptions[0].fact_ids == ["f1"]


def test_team_declaration_restores_violation() -> None:
    where = SourceRef(path=".fifthnine.yml", line_start=23, line_end=24)
    verdict, assumptions, _ = derive_verdict("fail", [fact("f1", exposure=OverrideExposure.ENV_POSSIBLE)], [], [TESTED],
                                             team_declarations={AssumptionKind.EXTERNAL_OVERRIDE_ENV: where})
    assert verdict is Verdict.VIOLATION and assumptions[0].state is AssumptionState.DECLARED_BY_TEAM


def test_missing_value_is_unknown_not_conditional() -> None:
    assert derive_verdict("fail", [fact("f1", status=FactStatus.UNRESOLVED)], [], [TESTED])[0] is Verdict.UNKNOWN
    assert derive_verdict("indeterminate", [fact("f1")], [], [TESTED])[0] is Verdict.UNKNOWN
    assert derive_verdict("fail", [fact("f1")], [link(LinkStatus.UNRESOLVED)], [TESTED])[0] is Verdict.UNKNOWN


def test_rule_context_assumption_makes_failure_conditional() -> None:
    extra = Assumption(kind=AssumptionKind.CLUSTER_ADMISSION_DEFAULT, statement="no LimitRange default", material=True)
    assert derive_verdict("fail", [fact("f1")], [], [TESTED], rule_assumptions=[extra])[0] is Verdict.CONDITIONAL


def test_clean_failure_is_violation() -> None:
    assert derive_verdict("fail", [fact("f1")], [link(LinkStatus.DECLARED)], [TESTED]) == (Verdict.VIOLATION, [], None)


# ---------------------------------------------------------------- D3: linkage provenance and invalidation


def test_inferred_and_stale_links_cap_at_conditional() -> None:
    for status in (LinkStatus.INFERRED, LinkStatus.STALE):
        verdict, assumptions, _ = derive_verdict("fail", [fact("f1")], [link(status)], [TESTED])
        assert verdict is Verdict.CONDITIONAL and assumptions[0].kind is AssumptionKind.LINKAGE_INFERRED


def test_declared_link_needs_location_and_unchanged_digest() -> None:
    with pytest.raises(ValidationError):
        link(LinkStatus.DECLARED, declared_in=None)
    with pytest.raises(ValidationError, match="stale"):
        link(LinkStatus.DECLARED, current_digest="sha256:changed")
    with pytest.raises(ValidationError):
        link(LinkStatus.STALE, current_digest="sha256:aa")


def test_links_accept_config_requires_digest_and_date() -> None:
    w = WorkloadRef(name="orders-service", container="orders-service")
    with pytest.raises(ValidationError):
        ServiceDecl(name="s", workload=w, accepted_by="links-accept")
    assert ServiceDecl(name="s", workload=w, accepted_by="links-accept", accepted_on="2026-10-21", evidence_digest="sha256:aa")


@pytest.mark.parametrize("name", ["links-accepted", "declared"])
def test_example_configs_validate(name: str) -> None:
    FifthNineConfig.model_validate(yaml.safe_load((ROOT / f"docs/fifthnine/m0/simulated/config/orders-service-spring.{name}.fifthnine.yml").read_text()))


# ---------------------------------------------------------------- D9: tested versions only


def test_violation_requires_tested_versions() -> None:
    with pytest.raises(ValidationError, match="D9"):
        finding(Verdict.VIOLATION, applicability=[UNTESTED])
    assert finding(Verdict.VIOLATION, applicability=[TESTED])


def test_untested_version_failure_is_conditional() -> None:
    verdict, assumptions, _ = derive_verdict("fail", [fact("f1")], [], [UNTESTED])
    assert verdict is Verdict.CONDITIONAL and assumptions[0].kind is AssumptionKind.UNTESTED_VERSION


def test_unsupported_versions_are_not_evaluated() -> None:
    with pytest.raises(ValueError):
        derive_verdict("fail", [fact("f1")], [], [UNSUPPORTED])
    with pytest.raises(ValidationError):
        finding(Verdict.SATISFIED, applicability=[UNSUPPORTED])


def test_support_matrix_claims_nothing_tested_yet() -> None:
    matrix = SupportMatrix.model_validate(yaml.safe_load((ROOT / "support/matrix.v1.yaml").read_text()))
    assert all(c.tested_versions == [] for c in matrix.components)
    assert all(e.kind != "runtime_fixtures" for c in matrix.components for e in c.other_evidence)


def test_only_violations_can_block() -> None:
    with pytest.raises(ValidationError):
        finding(Verdict.CONDITIONAL, blocking_eligible=True)
