# Simulated output (M0, schema revision 2)

SIMULATED. No scanner exists yet. Facts and each rule's check outcome were written by hand from the dev answer keys in `benchmarks/dev/`. Verdicts, assumptions and `verdict_if_assumptions_hold` were not hand-written: `scripts/m0_simulate.py` computes them with the normative `derive_verdict()`, so these files exercise the D2 and D9 logic the engine must use. Every JSON file has `"simulated": true`; every console file carries a SIMULATED banner and a note saying whether versions are projected as tested. `tests/m0/test_m0_contract.py` validates each file against the JSON Schema and the Pydantic models, checks every cited location, and checks the counts below.

| File | Scenario |
|---|---|
| `orders-service-spring.default` | `fifthnine scan .`, no `.fifthnine.yml`; versions projected as tested |
| `orders-service-spring.links-accepted` | after `fifthnine links accept` (`config/orders-service-spring.links-accepted.fifthnine.yml`) |
| `orders-service-spring.declared` | accepted, plus team declarations `external_overrides: none` and `platform.admission_defaults: none` (`config/orders-service-spring.declared.fifthnine.yml`) |
| `orders-service-spring.today-untested` | default mode with today's support matrix: no version tested |
| `catalog-api-fastapi.default` | `fifthnine scan .`, no `.fifthnine.yml`; versions projected as tested |

## Counts

| Scenario | Status | VIOLATION | CONDITIONAL | UNKNOWN | SATISFIED | Would block at >= high |
|---|---|---|---|---|---|---|
| Spring, default | FAIL_VERIFIED | 2 | 6 | 0 | 3 | 0 |
| Spring, links accepted | FAIL_VERIFIED | 2 | 6 | 0 | 3 | 0 |
| Spring, accepted + declarations | FAIL_VERIFIED | 6 | 2 | 0 | 3 | 0 |
| Spring, today (untested versions) | REVIEW_REQUIRED | 0 | 8 | 0 | 3 | 0 |
| FastAPI, default | FAIL_VERIFIED | 2 | 3 | 0 | 2 | 0 |

Blocking is 0 everywhere because no rule is promoted to blocking yet (D5).

## What to look at

**Accepting linkage alone does not create violations.** In the Spring sample, the shutdown, probe and actuator findings stay CONDITIONAL after `links accept`, because the Deployment pulls environment from an out-of-repo Secret that could override the Spring keys involved (revised D2). Each shows its computation, the result the repository evidence gives ("VIOLATION if the assumptions below hold") and the open assumption by name.

**Team declarations are cited, not hidden.** In the declared scenario the same findings become VIOLATION and print "relies on team declaration ... (.fifthnine.yml:23)". The HPA finding does the same with `admission_defaults`.

**Untested versions cap everything.** With today's empty support matrix, the Spring scan has no violations at all: eight CONDITIONAL findings, each carrying an `untested_version` assumption, and status REVIEW_REQUIRED. This is what a real scan would print before M2/M3 fixtures exist.

**FastAPI without a config file.** Two VIOLATIONs need no app-to-workload link and no external config: the unbounded tenacity retry, and gunicorn's 60s drain against a 30s grace period (the CLI flag is applied after environment settings, so the Secret cannot change it). The retry-deadline finding is CONDITIONAL because its 4s deadline is a settings default the external Secret could override.

**Never shown.** No "PASS", no "production ready". Satisfied checks list their assumptions. The disclaimer prints on every run.

These samples were written by the CTO with planted defects. They show the intended reporting behaviour; they are not evidence of precision or of performance on real repositories.
