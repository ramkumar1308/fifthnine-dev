# Schema revision 2 (M0): breaking changes for D2, D3 and D9

Date: 2026-10-21. Written before the changes were applied, per CEO instruction.

## Why the identifier stays `fifthnine.result/v1`

The v1 policy says breaking changes need v2. No engine has emitted v1 output; the only v1 documents are the M0 simulated files, which are regenerated in the same change. Shipping a v2 before any consumer has seen v1 would leave a dead version in the record. I am revising v1 in place and recording it as revision 2. From M1 onward, the first JSON emitted by the real CLI freezes v1 for good, and any later breaking change goes to v2. If the CEO prefers a v2 now, the change is a one-line identifier bump with no other effect.

## Changes to `schema/v1.py` (result)

| # | Change | Breaking? | Reason |
|---|---|---|---|
| 1 | `Finding.assumptions`: `list[str]` becomes `list[Assumption]`. `Assumption` = `kind`, `statement`, `material`, `state` (`open` or `declared_by_team`), `fact_ids`, `declared_in` | Yes (type change) | D2 needs to know which assumptions are material to the verdict and whether a team has declared them. Free text cannot carry that |
| 2 | New `AssumptionKind`: `external_override_env`, `external_override_file`, `linkage_inferred`, `classpath_unresolved`, `cluster_admission_default`, `platform_behavior`, `out_of_repo_routing`, `untested_version` | Additive enum | Machine-readable reason for every CONDITIONAL |
| 3 | New `Finding.verdict_if_assumptions_hold` (`VIOLATION`, `SATISFIED` or null) | Additive | D2: keep the repository-derived result visible when the verdict is CONDITIONAL |
| 4 | Model validators on `Finding`. VIOLATION: no open material assumption, every applicability entry `tested`, severity required. CONDITIONAL: at least one open material assumption, severity and `verdict_if_assumptions_hold` required. UNKNOWN: `unknown_reason` required, no `verdict_if_assumptions_hold` | Yes (stricter) | D2 and D9 become schema-enforced: a document that presents an override-exposed or untested-version result as VIOLATION fails validation |
| 5 | `Finding.applicability`: `dict[str, str]` becomes `list[ComponentSupport]`. `ComponentSupport` = `component`, `version`, `version_source`, `support` (`tested`, `untested`, `unsupported`), `tested_versions`, `matrix_entry` | Yes (type change) | D9: each finding states whether the detected versions are tested |
| 6 | `Inventory.detected_stacks`: `dict[str, str]` becomes `list[ComponentSupport]` | Yes (type change) | D9: the scan header shows support level per detected component |
| 7 | `LinkStatus` gains `stale` | Yes (new enum value; strict consumers break) | D3 invalidation: a declared link whose evidence changed is no longer authoritative |
| 8 | `Linkage` gains `declared_in`, `accepted_by`, `accepted_on`, `evidence_digest`, `current_digest` | Additive | D3 provenance |
| 9 | `blocking_ineligible_reasons()` simplified; new normative `derive_verdict()` | Function contract | Verdict logic for D2/D9 lives in one tested function the engine must use |
| 10 | `AssessmentLimits` gains `findings_conditional_on_external_config` and `findings_conditional_on_untested_version`; `findings_with_override_assumption` kept with the same meaning | Additive | Condition 4 counters for the new causes |

Removed: nothing. `conditional_reasons` stays as human-readable text.

## Changes to `schema/config_v1.py` (`.fifthnine.yml`)

| # | Change | Breaking? | Reason |
|---|---|---|---|
| 11 | `ServiceDecl` gains `accepted_on` (ISO date), `evidence_digest`, `evidence`. Required when `accepted_by: links-accept` | Yes for files written by `links accept` (none exist) | D3 provenance and invalidation |
| 12 | New top-level `platform.admission_defaults` (`unknown` default, or `none`) | Additive | Lets a team declare that no LimitRange injects requests, the one cluster-side input the first-wave rules depend on |
| 13 | `ExternalOverrides` unchanged in shape; meaning changes: `mode: none` now turns an open `external_override_env` assumption into `declared_by_team` (verdict can become VIOLATION) instead of only affecting blocking eligibility | Semantic | D2 |

## New file: `src/fifthnine/data/support_matrix.v1.yaml` with model `schema/support_v1.py`

Lists, per component, the approved range (E5), the versions with `tested` status and the evidence for each, and versions reviewed only. Every entry is `tested: []` today: M0 read source and ran some library behaviour, which is not the M2/M3 fixture standard. `support: tested` in a result requires the version to be listed here.

## Effect on the simulated outputs

| Scenario | Before (V/C/S) | After (V/C/S) | What moved |
|---|---|---|---|
| Spring, default | 3/5/3 | 2/6/3 | A7 HPA finding becomes CONDITIONAL (`cluster_admission_default`) |
| Spring, links accepted | 6/2/3 | 2/6/3 | A4, A5, A6 stay CONDITIONAL (`external_override_env` open) |
| Spring, links accepted + team declarations (new scenario) | n/a | 6/2/3 | Team declares `external_overrides: none` and `admission_defaults: none`; findings show the declaration they rely on |
| FastAPI, default | 3/2/2 | 2/3/2 | B3 retry-deadline finding becomes CONDITIONAL (`external_override_env`) |

Blocking-eligible counts drop to 0 in every scenario: under D5 no rule is blocking-capable until its runtime and boundary tests pass.
