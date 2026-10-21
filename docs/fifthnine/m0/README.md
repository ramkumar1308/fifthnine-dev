# M0 approval package

Date: 2026-10-21. Author: Claude (CTO). Approved by the CEO with corrections D2 and D9, now incorporated (schema revision 2). M1 has not started.

## Contents

| Deliverable requested | Where |
|---|---|
| Final architecture | `ARCHITECTURE.md` |
| Frozen CLI interface | `CLI.md` |
| Versioned Fact and Finding schemas | `SCHEMAS.md`, `SCHEMA_CHANGES_R2.md`, `src/fifthnine/schema/{v1,config_v1,support_v1}.py`, generated `schemas/*.v1.json` |
| Supported versions and limits (D9) | `src/fifthnine/data/support_matrix.v1.yaml` (no version tested yet) |
| Rule applicability and incumbent-overlap evidence | `RULES.md`, `INCUMBENT_RUNS.md`, raw outputs in `incumbent-raw/` |
| Version-specific test matrices for high-risk rules | `RULES.md`: FN-DEP-003 (S1-S19, P1-P7), FN-DEP-002 (R1-R17), FN-CFG-001 profiles (F1-F14), external overrides (X1-X11), plus matrices for the other six |
| Two representative sample repositories | `samples/orders-service-spring`, `samples/catalog-api-fastapi`; answer keys `benchmarks/dev/*.expected.yaml`; runtime evidence `benchmarks/dev/runtime_checks/` |
| Mocked console and JSON output, labeled simulated | `SIMULATED_OUTPUT.md`, `simulated/` |
| Updated DECISIONS.md and implementation contract | `../DECISIONS.md`, `../PHASE1_IMPLEMENTATION_CONTRACT.md` (amendments A1-A9 at top) |
| Unresolved issues blocking M1 | below |

Checks run on this package: `tests/m0/test_m0_contract.py` (34 passed): every simulated JSON file validates against the JSON Schema and the Pydantic models (including the D2, D3 and D9 invariants), every cited location exists in the sample, documented counts match, no rule is blocking, status derivation never yields a pass for empty, unknown or unassessable scans, and `derive_verdict()` behaves as specified for overrides, missing values, linkage, team declarations and untested or unsupported versions.

## Key results from the M0 review

1. On the two CTO-authored samples, every planted item that needs two artifacts (shutdown budget, probe target, rollout vs PDB, gunicorn drain, FastAPI probe path) was missed by all six incumbents at pinned versions. This describes planted defects only; it is not evidence of precision or of performance on real repositories.
2. Four statements in my M0 proposal were wrong and are corrected (DECISIONS.md ER-1 to ER-4). The largest: the FN-RES-002 design would have produced false findings against Resilience4j's default aspect order.
3. Two rules shrink: FN-RES-001 becomes Java-only, FN-DEP-001 ignores `maxUnavailable` PDBs. One becomes advisory-only (FN-CAP-002b).
4. Graceful shutdown default changed from IMMEDIATE (3.2, 3.3) to GRACEFUL (3.4, 3.5). The same repository gives different FN-DEP-003 results across that boundary. This is why version detection is a first-class engine stage.
5. After revision 2, external configuration and cluster-side defaults dominate the Spring sample: with versions projected as tested, 6 of 8 problem findings are CONDITIONAL, and accepting linkage alone does not change that because an out-of-repo Secret could override the Spring keys involved. With today's empty support matrix, the same scan shows 0 violations and 8 CONDITIONAL. M1/M2 will measure how often this happens on the dev corpus (condition 4).

## Gaps in this package

- The Spring sample was not compiled or run: Maven Central is unreachable from this environment. Spring semantics rest on tagged source reading. Rows R4 and R6 (management path edge cases) need a runtime fixture in M2 before they can block.
- The Semgrep registry and Pro rules were unreachable; only the open semgrep-rules repository was checked.
- Python library ranges (M0-D9) were verified at the latest release only.
- Samples are CTO-authored. They show coverage differences on planted items, not real-world precision.

## M1 readiness

Technical prerequisites for M1 are complete. Administrative blockers:

| # | Blocker | Owner |
|---|---|---|
| R1 | Independent FifthNine repository created and push access granted to this session (E1) | CEO |
| R2 | Confirm that v1 may be revised in place (revision 2) rather than bumped to v2 | CEO |

Needed later, not blocking M1:

| # | Issue | Needed by |
|---|---|---|
| L1 | Names of the independent benchmark author and outside SRE reviewer (E7) | before M3 ends |
| L2 | A machine or CI runner with Maven Central access for Spring runtime fixtures | M2 |
| L3 | Semgrep registry re-check of the overlap table | before M3 freeze |
| L4 | Dev corpus list published before held-out selection (M0-D8) | early M1 |
| L5 | Package name and ownership (M0-D10, deferred) | before public alpha |

## Files in the repository today

```
LICENSE                                  Apache-2.0
docs/fifthnine/PHASE1_IMPLEMENTATION_CONTRACT.md, DECISIONS.md, M0_AUDIT_AND_PROPOSAL.md
docs/fifthnine/m0/                       this package
src/fifthnine/schema/                    frozen v1 models, revision 2 (no engine code)
src/fifthnine/data/support_matrix.v1.yaml                   tested-version matrix (D9)
schemas/                                 generated JSON Schemas
scripts/                                 export_schemas.py, m0_simulate.py, m0_run_incumbents.sh
samples/                                 two sample repositories
benchmarks/dev/                          dev answer keys and runtime checks (not held-out)
tests/m0/                                contract tests for this package
```
