# HX1: head-to-head screening protocol (pre-registration)

Status: PROPOSED, awaiting CEO approval. Nothing in this protocol has been executed.
Author: Claude (CTO). Date: 2026-10-21.
Freeze rule: once approved, the commit that contains this file is the frozen protocol. Scoring rules, case content, prompts and model settings do not change after any contender output has been seen. A rule that proves impossible to follow is logged as a deviation, and results are reported under both the frozen rule and the deviation, side by side.

## 1. Question and scope

The CEO's final question is: does FifthNine provide reliable, independently verifiable production-readiness checks that meaningfully improve on existing scanners and AI code review, enough for engineers to want it as a recurring CI check?

HX1 is a screening experiment. Its scope:

| HX1 can show | HX1 cannot show |
|---|---|
| How completely, correctly and repeatably a well-configured AI reviewer already handles FifthNine's target checks on the planted cases | Whether FifthNine's own checks are reliable. No rules exist until M3; FifthNine's column is the expected output of the approved rule specs, not an executed result |
| Where AI reviewers and pinned incumbent scanners miss, misjudge or overstate, so M2/M3 can focus there | Market superiority or real-world precision. The cases were written by the CTO (CEO decision 3); M5 uses independently authored cases and unfamiliar repositories |
| CI execution characteristics (time, cost, egress, repeatability, gating) | Whether engineers would adopt FifthNine. That needs the practitioner reviews and budget-owner interviews already in the contract |

There is no kill threshold (CEO decision 2). Results are reported per dimension. No single number decides.

## 2. Contenders

| ID | Contender | Configuration (recorded exactly in the run log) |
|---|---|---|
| C1 | GitHub Copilot code review | Review on a pull request that adds the whole case repository to an empty `main`. Repository instructions in `.github/copilot-instructions.md` (section 6), effort level Balanced (documented default), tool calling and deterministic detections enabled, Copilot Memory off, default model selection (GitHub does not support model switching for review). Licence: CEO account |
| C2 | Frontier model, repository-aware agent | One frontier model chosen by the CEO, run in its vendor's coding agent with the case repository checked out, read-only file and shell tools, web access disabled. Same instruction text as C1, given as the task prompt and also present as `.github/copilot-instructions.md`. Exact model ID, agent version, temperature or effort setting recorded before run 1 |
| I | Incumbent scanners | kube-linter 0.8.3, Checkov 3.3.30, Hadolint 2.15.1, Ruff 0.17.1, Bandit 1.9.4, Semgrep 1.182.0 with semgrep-rules @16b9e6d, as in M0. Run once per case (deterministic) |
| F | FifthNine expected | The verdicts the approved rule specs (`docs/fifthnine/m0/RULES.md`, schema rev 2) give for each item, from the dev answer keys. Labelled "expected, not executed" in every table |

Why the CTO cannot be C2: Claude wrote the cases and answer keys in this conversation, so its outputs would be contaminated. C2 must be run in a fresh session by the CEO, the co-founder, or an independent person, with no access to this repository's answer keys or docs.

Both C1 and C2 get the strongest reasonable configuration (CEO decision 4). The instructions include the full FifthNine rule list in plain language, which helps the contenders. That is deliberate: HX1 measures the case where a team has already written our checklist into their AI reviewer.

## 3. Cases

Four blinded case repositories are built from the M0 samples. Blinding applies to every case: package, service, namespace and host names are changed (for example `orders-service` becomes `ledger-api`), the `samples/` path and all FifthNine references are removed, and dev answer keys and docs are excluded. Code comments that a real team could have written (such as the `INC-2291` note) stay. Each case lives in its own new private repository, so contenders cannot retrieve the public originals in `ramkumar1308/fifthnine-dev`.

| Case | Built from | Change from the M0 sample | Purpose |
|---|---|---|---|
| K1 | orders-service-spring | Renames only (Spring Boot 3.4.13) | Spring, cross-file defects |
| K2 | orders-service-spring | Renames, plus Spring Boot parent changed to 3.3.13 | Version flip: two verdicts change |
| K3 | catalog-api-fastapi | Renames only | Python, cross-file defects |
| K4 | catalog-api-fastapi | Renames, plus every planted defect fixed (section 4) | Clean control: every report of a planted issue is a false positive |

The case content is frozen as a git tree hash for each case, recorded in the run log before run 1.

## 4. Items and reference labels

Reference labels:
- `DEFECT`: a real reliability problem the repository shows. A good reviewer should report it.
- `CONTEXT`: a real problem whose effect depends on something outside the repository (a classpath, a cluster LimitRange, a GitOps setting, an out-of-repo Secret). A good reviewer reports it and states the dependency.
- `TRAP`: looks like a problem but is not one, for the stated reason. Reporting it as a defect is a false positive.

### K1 (Spring Boot 3.4.13)

| Item | Location | Reference | Mechanism the reviewer must identify |
|---|---|---|---|
| A1 | PaymentClient.java:12 | DEFECT | `new RestTemplate()` has no connect or read timeout |
| A2 | ShippingQuoteClient.java:13 | CONTEXT | Static `RestClient.builder()` ignores `spring.http.client.*`; timeout depends on the classpath |
| A4 | application.yml:5 + deployment.yaml:57-59 | DEFECT | preStop 10s + 45s shutdown phase exceeds the 30s default grace period; graceful shutdown is on by default in 3.4 |
| A5 | deployment.yaml:44-47 + application.yml:16 | DEFECT | Readiness probe on 8080, actuator served only on management port 8081 |
| A6 | application-prod.yml:7 + deployment.yaml:32-33 | DEFECT | Active prod profile exposes all actuator endpoints |
| A7 | hpa.yaml:16-19 + deployment.yaml:40-41 | CONTEXT | CPU-utilization HPA with no CPU request; LimitRange could inject one |
| A8 | deployment.yaml:9 | CONTEXT | `spec.replicas` set while an HPA manages the Deployment |
| A9 | deployment.yaml:13 + pdb.yaml:7 | DEFECT | 50% maxUnavailable lets ready pods fall below the 75% PDB floor during rollout |
| T1 | application-dev.yml | TRAP | Risky actuator and stack-trace settings are in the inactive dev profile |
| T2 | service.yaml, ingress.yaml | TRAP | Management port is ClusterIP only; Ingress does not reach actuator |
| T3 | application.yml base URLs | TRAP | localhost fallbacks are overridden by the in-repo ConfigMap |
| T4 | InventoryClient.java:15 | TRAP | Injected builder receives the 2s read timeout on 3.4 |
| T5 | deployment.yaml liveness probe | TRAP | Liveness probe on `management` port is correct |

Unplanted, scored only if reported: X1, POST `/v1/charges` retried 4 times with no idempotency evidence. A report counts as a valid extra finding.

### K2 (Spring Boot 3.3.13, otherwise identical to K1)

All K1 items are scored again. Two change, and only these two count toward version accuracy:

| Item | Reference on 3.3.13 | Why it differs from K1 |
|---|---|---|
| A3v | DEFECT | `spring.http.client.read-timeout` does not exist in 3.3, so InventoryClient has no read timeout (T4 becomes a defect) |
| A4v | TRAP for the stated mechanism | Graceful shutdown is off by default in 3.3, so the 55s-versus-30s drain budget does not apply. A report that in-flight requests are dropped because graceful shutdown is off is a different, valid finding, adjudicated as an extra |

### K3 (FastAPI)

| Item | Location | Reference | Mechanism |
|---|---|---|---|
| B1 | recommendations.py:6 | DEFECT | httpx client with `timeout=None` (Ruff and Bandit also flag it; FifthNine delegates it) |
| B8 | pricing.py:7 | DEFECT | `requests.get` without timeout (delegated, as B1) |
| B2 | inventory.py:21 | DEFECT | tenacity `@retry` with no `stop=` retries forever on a request path |
| B3 | products.py:13-14 + inventory.py:9-15 + settings.py:8 | CONTEXT | Worst-case retry window 8s exceeds the 4s outer `asyncio.timeout`; deadline is an env-overridable setting |
| B4 | deployment.yaml:27 + :17 | DEFECT | gunicorn `--graceful-timeout=60` exceeds 30s grace period |
| B5 | deployment.yaml:40-43 + health.py:11 | DEFECT | Readiness probe `/readyz`; app serves `/ready` |
| B7 | deployment.yaml:8 | CONTEXT | `spec.replicas` with an HPA |
| T6 | pricing.py:20 | TRAP | `httpx.get` without timeout uses the 5s default |
| T7 | Dockerfile:11 | TRAP | `--reload` in CMD is replaced by the Deployment command |
| T8 | hpa.yaml | TRAP | AverageValue CPU target does not need requests (HPA works) |
| T9 | pdb.yaml | TRAP | `maxUnavailable: 1` PDB with default rollout is not a declared capacity floor |
| T10 | service.yaml | TRAP | LoadBalancer on port 80 is the public API, not an admin port |
| T11 | inventory.py:6 | TRAP | Inventory client has an explicit 2s timeout |

### K4 (FastAPI, fixed)

Fixes applied: B1 `timeout=5.0`; B8 `timeout=5`; B2 `stop=stop_after_attempt(3)`; B3 two attempts with 1.5s timeout and 0.5s wait (3.5s, inside 4s); B4 `--graceful-timeout=25`; B5 probe path `/ready`; B7 `spec.replicas` removed. Items F1 to F7 (one per fix) are scored as TRAP, together with T6 to T11. K4 has no DEFECT items.

## 5. Procedure

1. CTO builds K1 to K4 and records each tree hash. CTO builds the instruction file (section 6) and the run log template.
2. CEO creates the private repositories and confirms settings for C1 and C2 (model, versions, effort), recorded in the run log.
3. C1, for each case: five pull requests from five identical branches, each requesting one Copilot review. Separate PRs, so no run sees an earlier run's comments.
4. C2, for each case: five fresh sessions, same prompt, same repository state, no memory carried between sessions.
5. Incumbents: one run per case with the pinned versions.
6. Raw outputs (review comments with file and line, full agent transcripts, scanner output) are exported unedited, with wall time and cost per run.
7. CTO anonymizes C1 and C2 outputs as contender X and Y (assignment by coin flip, recorded in a sealed note held by the CEO) and maps each reported issue to an item or to "extra".
8. Adjudication (section 7), then scoring (section 8), then the report.

Total runs: 4 cases x 2 contenders x 5 runs = 40, plus 4 incumbent runs. C1 cost per GitHub's estimate: $0.25 to $5 per Balanced review, so at most about $100 for 20 reviews, plus Actions minutes. C2 cost is recorded from the vendor's billing.

## 6. Instruction text given to C1 and C2

Identical text for both, placed in `.github/copilot-instructions.md` and, for C2, also sent as the task prompt:

> You are reviewing this repository as an experienced site reliability engineer performing a production readiness review before it ships to Kubernetes. Review the whole repository: application code, application configuration including Spring profiles, Dockerfiles and Kubernetes manifests, and how they interact. Use the exact framework and library versions declared in the build files when reasoning about defaults.
>
> Check at least: HTTP client timeouts; retry bounds, backoff and retry windows against any outer deadline; graceful shutdown timing against the pod termination grace period, including preStop hooks; readiness and liveness probe targets against the ports and paths the application actually serves; risky settings in the profile that is active in production; management or admin endpoints reachable from outside the cluster; autoscaler configuration against resource requests; spec.replicas on autoscaled Deployments; rolling update settings against PodDisruptionBudgets.
>
> For every issue give: file and line, the mechanism, the framework or Kubernetes behaviour it depends on, and severity. If the issue depends on something not in the repository (cluster defaults, external secrets, deployment tooling, transitive dependencies), say so explicitly and mark it as needing verification. Do not report something as a problem if the repository shows it is handled. Report only production reliability issues; skip style and general security hygiene.

## 7. Adjudication

- Each reported issue is mapped to an item, or to "extra" if it matches none.
- Extras are judged valid or invalid by an adjudicator who sees contender labels X and Y only. The adjudicator is the outside SRE reviewer if named before scoring; otherwise the CEO and the co-founder jointly. The CTO proposes a label with its reason; the adjudicator decides. Disagreements are reported, not resolved silently.
- The CTO does not see the X/Y identity before adjudication is complete.

## 8. Scoring rules (frozen on approval)

Per run, each item gets one classification:

| Classification | Rule |
|---|---|
| D (detected) | Correct location (file, and line within 3 lines of a cited location) and the correct mechanism |
| W (wrong reason) | Correct location, mechanism missing or wrong (for example, right file but wrong version reasoning) |
| U (uncertain) | Raised as possible or needing verification, with the correct location |
| M (missed) | Not raised |
| FP (false positive) | A TRAP reported as a definite defect |
| N (neutral) | A TRAP mentioned without calling it a defect, or noted as fine |

Dimensions, each reported per contender and per case:

1. Correctness: recall over DEFECT and CONTEXT items (D counts 1, U counts 0.5, W counts 0), and precision over all reported reliability issues after adjudication (valid / total reported).
2. Repeatability: share of items with the same classification in 5 of 5 runs; mean pairwise Jaccard similarity of the sets of issues reported per run.
3. False positives: FP count on TRAP items per run, plus invalid extras per run.
4. Version accuracy: on A3v and A4v in K2, and their K1 counterparts T4 and A4, whether the classification matches the version. Reported as counts, because N is small.
5. Uncertainty handling: on CONTEXT items, the share of D or U reports that state the external dependency.
6. Evidence quality: on each D, 2 points for correct location and mechanism citing the governing behaviour or version; 1 for correct location with a vague mechanism; 0 otherwise. Reported as a mean.
7. CI execution characteristics: wall time and cost per run, whether code leaves the machine, whether output can gate a merge (per vendor documentation), setup steps.
8. Unique actionable findings: valid items or extras found by exactly one contender in at least 3 of 5 runs.

FifthNine's column is filled from the rule specs before any contender output is seen, and committed with the run log.

## 9. Report structure

1. Case-level matrix. Rows: every item in K1 to K4. Columns: C1 and C2, each as a D/W/U/M/FP/N count over 5 runs; I as a single result; F as the expected verdict.
2. Dimension table: the eight dimensions per contender.
3. Technical feasibility: where the contenders were incomplete, inconsistent or wrong, and whether FifthNine's approved rule specs give a deterministic, version-correct verdict there. States which of those verdicts M0 already backed with source or runtime evidence, and which wait for M2/M3 fixtures.
4. Commercial differentiation, kept separate: what the evidence suggests about cost, gating and overlap, and what it cannot settle. The anonymized matrix goes to the practitioner reviews required by the contract.
5. Answer to the CEO's question at the strength the evidence allows, with the limits from section 1.
6. Deviations log, if any.

## 10. Roles and timeline

| Step | Owner | Duration |
|---|---|---|
| Build K1 to K4, instructions, run log template | CTO | day 1 |
| Create private repos, record C1/C2 settings, run 40 contender runs, export outputs | CEO (or a delegate with no access to answer keys) | days 2 to 3 |
| Incumbent runs, anonymized mapping | CTO | day 3 |
| Adjudication of extras | Outside SRE reviewer, or CEO and co-founder | day 4 |
| Scoring and report | CTO | day 5 |

M2 preparation continues in parallel. The locked Phase 1 product does not change.

## 11. Decisions needed before day 1

1. Approve this protocol as written, or amend it now.
2. Choose C2's model and agent, and who runs C1 and C2.
3. Name the adjudicator (outside SRE reviewer, or CEO and co-founder).
4. Confirm the budget: up to about $100 for C1, plus C2 usage.
