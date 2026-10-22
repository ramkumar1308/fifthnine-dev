# Fact, Finding and Result schemas, v1 revision 2

Source of truth: `src/fifthnine/schema/v1.py`, `config_v1.py` and `support_v1.py` (Pydantic v2). Generated JSON Schemas: `schemas/result.v1.json`, `config.v1.json`, `baseline.v1.json`, `support-matrix.v1.json` (`PYTHONPATH=src python scripts/export_schemas.py`).

JSON Schema describes shape only. The cross-field rules below (verdict invariants, linkage provenance) are model validators: a consumer that wants them must validate with the Pydantic models or reimplement them. `tests/m0/test_m0_contract.py` covers each one.

## Versioning policy

`schema` is `fifthnine.result/v1`. Revision 2 (D2, D3, D9) changed v1 in place before any engine emitted it; `SCHEMA_CHANGES_R2.md` lists every change and why. The first JSON emitted by the M1 CLI freezes v1. After that, only additive optional fields are allowed within v1; anything else needs v2 and CEO approval.

## Verdicts (C1)

| Verdict | Meaning | Enforced invariants |
|---|---|---|
| VIOLATION | The check failed and every material input is established | Severity required. No open material assumption. Every applicability entry `tested` |
| CONDITIONAL | The check fails on repository evidence, but at least one material assumption is open (external config, inferred or stale linkage, classpath, admission defaults, platform behaviour, routing, untested version) | Severity required. At least one open material assumption. `verdict_if_assumptions_hold` set |
| UNKNOWN | A material input has no value in the repository, or the check could not be computed | `unknown_reason` required |
| SATISFIED | This specific check passed for this subject, within `applicability` and listed assumptions | Assumptions may be open and are shown |

No finding may name an `unsupported` component version; those rules are not run and a `COV-UNSUPPORTED` notice is emitted. Only VIOLATION findings can be blocking-eligible. A rule that does not apply emits nothing; "not applicable" is not SATISFIED.

## Assumptions

`Assumption` = `kind`, `statement`, `material`, `state`, `fact_ids`, `declared_in`. `state` is `open` (FifthNine assumed it) or `declared_by_team` (stated in `.fifthnine.yml`; `declared_in` is required). Declarations available in v0.1: `services[].external_overrides.mode: none` (external override assumptions) and `platform.admission_defaults: none` (admission-default assumption).

## Support levels (D9)

`ComponentSupport.support` is `tested` only if the version appears under `tested_versions` in `src/fifthnine/data/support_matrix.v1.yaml` with runtime-fixture evidence. Source review and spot checks never make a version tested. Every result lists support per detected component in `inventory.detected_stacks`, and each finding repeats the entries it depends on in `applicability`.

## Linkage provenance (D3)

`Linkage.status`: `declared`, `inferred`, `stale`, `unresolved`. Declared and stale links must cite `declared_in`. Links written by `fifthnine links accept` must carry `accepted_on` and `evidence_digest`. A declared link whose `current_digest` differs from `evidence_digest` must be reported as `stale`.

## Scan status (C2)

Derived by `derive_status()`: any unsuppressed VIOLATION gives `FAIL_VERIFIED`; else any CONDITIONAL gives `REVIEW_REQUIRED`; else any UNKNOWN, any blocking coverage gap, or zero rules evaluated gives `INCOMPLETE`; else `PASS_WITH_COVERAGE`. There is no `PASS` and no "production ready" status. The fixed disclaimer is in every result.

## Blocking eligibility

`blocking_ineligible_reasons()` returns an empty list only for a VIOLATION from a rule promoted to blocking (D5), with every linkage it uses declared, and not suppressed. The gate fails when an eligible finding's severity is at or above `fail_on`. In v0.1 no rule is promoted yet.

## Fingerprints

`sha256(rule_id + "\n" + subject.key)`. Subject keys use stable identities, not line numbers or values.

## Simulation flag

`simulated: true` is allowed only in M0 mock output. The engine never sets it.

## Additive changes since the revision 2 freeze

| Date | Schema | Change |
|---|---|---|
| 2026-10-21 (M1) | support-matrix v1 | `ComponentEntry.specifier` (PEP 440) and `ComponentEntry.allowed_versions` (exact or leading-component matches) give approved ranges a machine-readable form |
| 2026-10-22 (M2-1) | result v1, revision 3 | `Fact.inputs` (role, source or fact id), `Fact.exposure_sources` (via, object, variables), `Fact.link_ids`, `Fact.precedence`, `Fact.absent`; `DefaultRef.applied_version` and `DefaultRef.upstream` (repository, tag, commit, path, line, anchor, `verified_at`). All optional. **Strict-consumer note:** a consumer that rejects unknown properties must be updated; revision 2 documents remain valid under the revision 3 schema. `exposure_sources` is required on engine output whenever exposure is not `none`; the model does not enforce this, so that revision 2 documents still load. |
