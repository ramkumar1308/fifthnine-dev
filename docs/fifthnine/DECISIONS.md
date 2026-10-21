# FifthNine Scan decision log

Source of truth for decisions, together with `PHASE1_IMPLEMENTATION_CONTRACT.md`. Read both at the start of every session. Entries are append-only; a superseded entry is marked, not deleted.

Status values: APPROVED (CEO), PROPOSED (awaiting CEO), ERRATUM (correction of an earlier CTO statement).

---

## 2026-10-21: CEO review of the M0 audit and proposal

Overall: M0 approved with conditions. M1 not authorized until the M0 approval package is accepted.

| ID | Decision | Status |
|---|---|---|
| E1 | New independent FifthNine repository outside SasquatchLabsInc, Apache-2.0. Repository creation and access arranged separately; CTO must not assume push access until confirmed | APPROVED |
| E2 | No FN-DATA migration rules in v0.1. Data reliability stays a future domain. Document delegation to Squawk and Atlas | APPROVED |
| E3 | v0.1 accepts already-rendered Helm/Kustomize output. Measure real-world coverage gaps; recommend native rendering later if warranted | APPROVED |
| E4 | Only authoritative, declared service linkages may support blocking cross-artifact findings. Inferred linkages are advisory. Explore easy confirmation of proposed linkages without making manual mapping the normal experience | APPROVED |
| E5 | Initial support: Spring Boot 3.2-3.5, Java 17/21, Python 3.10-3.13 | APPROVED |
| E6 | The ten checks are candidates, not frozen rules. Validate framework semantics, applicability, incumbent overlap and false-positive cases first. Particular attention: graceful shutdown timing, probe endpoint resolution, effective Spring profiles, external overrides | APPROVED (conditional) |
| E7 | Independent benchmark author and outside SRE reviewer required; identities pending. Implementation and held-out evaluation stay independent | APPROVED |
| E8 | External SARIF aggregation is a stretch goal, not an M4 commitment | APPROVED |

Conditions attached to M0 approval:

| ID | Condition | Where addressed |
|---|---|---|
| C1 | Clear distinction among VIOLATION, SATISFIED, CONDITIONAL, UNKNOWN | `m0/SCHEMAS.md`, `schema/v1.py` Verdict |
| C2 | No production-ready classification from absence of findings | `derive_status`, fixed disclaimer, `tests/m0` |
| C3 | Test against a realistic single-service repository without `.fifthnine.yml` | both samples, default-mode simulated output |
| C4 | Measure how often unresolved linkages or unsupported configuration prevent assessment | `summary.assessment_limits` in every result; corpus measurement in M1/M2 (M0-D8) |
| C5 | Keep deterministic verification separate from any future AI advisory | `advisory` section, `max_length=0` in v1 |
| C6 | Document running FifthNine alongside security and IaC scanners | `m0/CLI.md` "Running next to other scanners"; `docs/coverage.md` at M4 |
| C7 | Technical review of the ten candidates against exact pinned framework and tool versions | `m0/RULES.md`, `m0/INCUMBENT_RUNS.md` |

Scope reaffirmed: no enterprise control plane, Critical Business Journey engine, Failure Graph or cross-repository verification platform in this release.

---

## 2026-10-21: CTO proposals in the M0 approval package

| ID | Proposal | Status |
|---|---|---|
| M0-D1 | Split the contract's `INCOMPLETE/REVIEW_REQUIRED` status into `REVIEW_REQUIRED` (conditional findings, no violations) and `INCOMPLETE` (unknowns, blocking coverage gaps, or nothing assessable) | PROPOSED |
| M0-D2 | Override exposure policy. Out-of-repo file mounted where the framework reads config: dependent results UNKNOWN. Out-of-repo env source attached (or runtime context unknown): verdict kept, assumption recorded, finding blocking-ineligible until the service declares `external_overrides` in `.fifthnine.yml` | PROPOSED |
| M0-D3 | Add `fifthnine links` and `fifthnine links accept` to the frozen CLI as the E4 confirmation path. `accept` shows the YAML it will write and refuses ambiguous proposals | PROPOSED |
| M0-D4 | FN-RES-001 is Java-only in v0.1. Python HTTP timeout problems are delegated to Ruff S113 / Bandit B113 and surfaced as `COV-DELEGATED` coverage entries with locations | PROPOSED |
| M0-D5 | Rule refinements from the M0 review: FN-RES-002 needs an explicit outer deadline; FN-DEP-001 uses PDB `minAvailable` only; FN-CAP-002b advisory-only; FN-DEP-003 severity low when every input is a default; FN-RES-004 CONDITIONAL off request paths | PROPOSED |
| M0-D6 | JSON output has no timestamps or durations by default (`--timing` adds them), so repeated scans are byte-identical | PROPOSED |
| M0-D7 | Kubernetes support in v0.1: Deployment, Service, Ingress, HPA (autoscaling/v2), PDB (policy/v1), ConfigMap. StatefulSet, DaemonSet, Gateway API, Argo Rollouts later | PROPOSED |
| M0-D8 | Condition 4 measurement: CTO selects a dev corpus of public Spring Boot and FastAPI repositories with in-repo manifests (target 20, licenses recorded) and publishes the list. The independent benchmark author excludes those repositories from held-out data. This removes any need for the CTO to see held-out selections | PROPOSED |
| M0-D9 | Python library ranges supported at launch, to be verified per range in M2: tenacity 8.x-9.x, httpx 0.27-0.28, gunicorn 21-26, uvicorn 0.30+, FastAPI 0.100+. Only the latest of each was verified in M0 | PROPOSED |
| M0-D10 | Distribution name `fifthnine` on PyPI (unregistered as of 2026-10-21). Registering it early prevents squatting; needs the CEO's account decision | PROPOSED |

Outcome: decided by the CEO later the same day; see "CEO decision on the M0 approval package" below.

---

## 2026-10-21: Errata to the M0 audit and proposal

| ID | Original statement | Correction | Evidence |
|---|---|---|---|
| ER-1 | FifthNine would own Python `httpx` with `timeout=None` | Ruff 0.17.1 S113 and Bandit 1.9.4 B113 already flag it | `m0/incumbent-raw/ruff-*.txt`, `bandit-*.txt` |
| ER-2 | Ruff's docs say S113 flags `httpx` calls that omit `timeout` | Ruff 0.17.1 did not flag `httpx.get(url)` in sample B | same |
| ER-3 | FN-RES-002 compares Resilience4j retry with TimeLimiter "on the same instance" | Default aspect order puts Retry outside TimeLimiter, so TimeLimiter bounds each attempt; that comparison would produce false findings | resilience4j v2.4.0 `*ConfigurationProperties` aspect orders |
| ER-4 | FN-DEP-001 would compare rollout with the PDB generally | Using `maxUnavailable` PDBs would fire on many default configurations without expressing a capacity floor | sample B trap; `m0/RULES.md` FN-DEP-001 |

---

## 2026-10-21: CEO decision on the M0 approval package

M0 engineering foundation approved with two required corrections (D2, D9). M1 conditionally authorized once those are incorporated and repository access is ready. Do not start M2 or any Phase 2 capability. The first implementation milestone delivers a usable CLI foundation, not a dashboard, application graph or AI agent. Sample-specific scanner gaps must not be presented as proven real-world superiority.

| ID | CEO decision | Implemented as |
|---|---|---|
| M0-D1 | APPROVED | `ScanStatus` has `REVIEW_REQUIRED` and `INCOMPLETE` |
| M0-D2 | REVISED: when external configuration may override a value material to the verdict, do not present an unconditional VIOLATION. Keep the repository-derived calculation, state assumptions, use CONDITIONAL or UNKNOWN depending on whether the property can be evaluated under explicit assumptions | Schema revision 2: `Assumption` model, `verdict_if_assumptions_hold`, `Finding` validators, `derive_verdict()`. Value present but overridable: CONDITIONAL with computation kept. Value absent: UNKNOWN. Team declaration in `.fifthnine.yml`: VIOLATION citing the declaration. Applies equally to cluster admission defaults (`platform.admission_defaults`) |
| M0-D3 | APPROVED with explicit approval, provenance and invalidation safeguards | `links accept` confirms interactively, refuses without TTY unless `--yes`; entries record `accepted_on` and `evidence_digest`; changed digest gives `stale`, treated as inferred. `Linkage` provenance fields and validators |
| M0-D4 | APPROVED | FN-RES-001 Java-only; Python timeouts as `COV-DELEGATED` |
| M0-D5 | APPROVED provisionally, subject to runtime and boundary-case tests before rules become blocking | Every rule starts `blocking_capable = False`; promotion criteria in `m0/ARCHITECTURE.md`; promotions recorded here |
| M0-D6 | APPROVED | Deterministic JSON by default |
| M0-D7 | APPROVED | Kubernetes object scope as proposed |
| M0-D8 | APPROVED: publish the development corpus before independent held-out selection | Corpus list published in the repository before the benchmark author starts selecting |
| M0-D9 | REVISED: claim support only for tested versions; document untested ranges and applicability limits | `support/matrix.v1.yaml` (no version tested as of M0), `ComponentSupport` with `tested`/`untested`/`unsupported`, VIOLATION requires tested versions, untested gives CONDITIONAL with `untested_version`, unsupported is not evaluated |
| M0-D10 | DEFERRED until package name and ownership are confirmed | No registration; `fifthnine` used only as the working CLI/package name |

Schema changes: revision 2 of v1, explained before application in `m0/SCHEMA_CHANGES_R2.md`. Identifier kept as `fifthnine.result/v1` because no engine has emitted v1; the CEO may ask for a v2 identifier instead.

Contract tests after revision 2: 34 passed (`tests/m0/test_m0_contract.py`).

### M1 readiness (CTO, 2026-10-21)

Technical prerequisites for M1 are complete. Remaining blockers are administrative:

| # | Blocker | Owner |
|---|---|---|
| R1 | Independent FifthNine repository created and push access granted to this session (E1) | CEO |
| R2 | Confirmation that the schema revision 2 approach (v1 revised in place) is acceptable, or instruction to bump to v2 | CEO |

Needed later, not blocking M1: benchmark author and outside SRE reviewer (before M3 ends); a CI runner or machine with Maven Central access (M2); Semgrep registry re-check (before M3 freeze); package name and ownership (M0-D10, before alpha); dev corpus publication (early M1, before held-out selection).

M1 scope as authorized: installable `fifthnine` CLI, workspace discovery with ignores and untrusted-input limits, profile selection, stack and version detection with support levels, console and JSON output on the frozen schema, deterministic results, exit codes 0/1/2, no network. No rules beyond coverage notices, so M1 scans report `INCOMPLETE` with an honest inventory.
