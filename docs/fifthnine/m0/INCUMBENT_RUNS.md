# Incumbent scanner runs on the M0 sample repositories

Date: 2026-10-21. All tools ran offline against `samples/orders-service-spring` and `samples/catalog-api-fastapi`. Raw outputs are in `incumbent-raw/`. Re-run with `scripts/m0_run_incumbents.sh`.

## Pinned versions

| Tool | Version | How obtained |
|---|---|---|
| kube-linter | 0.8.3 | GitHub release binary |
| Hadolint | 2.15.1 | GitHub release binary |
| Checkov | 3.3.30 | PyPI, run with `--skip-download` (no platform calls) |
| Ruff | 0.17.1 | PyPI, `--select S,ASYNC,BLE` |
| Bandit | 1.9.4 | PyPI |
| Semgrep | 1.182.0 | PyPI, `--metrics=off`, local rules only |
| semgrep-rules | commit 16b9e6d (2026-10-21) | Subset: python/requests, python/lang/best-practice, java/spring, java/lang/best-practice, yaml/kubernetes, dockerfile (89 rules). The Semgrep registry (`semgrep.dev`) was unreachable, so registry-only and Pro rules are not covered |

kube-linter ran twice: default checks and `--add-all-built-in`.

## Planted item versus detection

"Hit" means the tool reported the specific reliability problem. "Adjacent" means it reported something on the same object that does not describe the problem.

| Item | FifthNine expected (default mode) | kube-linter | Checkov | Hadolint | Ruff | Bandit | Semgrep |
|---|---|---|---|---|---|---|---|
| A1 `new RestTemplate()` no timeouts | VIOLATION | n/a | n/a | n/a | n/a | n/a | miss |
| A2 static `RestClient.builder()` | CONDITIONAL | n/a | n/a | n/a | n/a | n/a | miss |
| A4 shutdown 45s + preStop 10s > grace 30s | CONDITIONAL (VIOLATION once linkage accepted) | miss | miss | miss | n/a | n/a | miss |
| A5 readiness probe on 8080, actuator on 8081 | CONDITIONAL (VIOLATION once accepted) | miss: `readiness-port` passes because 8080 is a declared containerPort | miss: CKV_K8S_8/9 pass (probes present) | n/a | n/a | n/a | miss |
| A6 prod profile exposes all actuator endpoints | CONDITIONAL (VIOLATION once accepted) | n/a | n/a | n/a | n/a | n/a | hit on `application-prod.yml:7`, plus a hit on inactive `application-dev.yml:9` |
| A7 HPA cpu Utilization, no cpu request | VIOLATION | adjacent: `unset-cpu-requirements` (no HPA link) | adjacent: CKV_K8S_10 | n/a | n/a | n/a | miss |
| A8 `spec.replicas` with HPA | CONDITIONAL | miss | miss | n/a | n/a | n/a | miss |
| A9 rollout 50% vs PDB minAvailable 75% | VIOLATION | miss | miss | n/a | n/a | n/a | miss |
| B1 httpx `timeout=None` | delegated | n/a | n/a | n/a | hit S113 | hit B113 | miss |
| B2 tenacity `@retry` without stop | VIOLATION | n/a | n/a | n/a | miss | miss | miss (no tenacity rule in the repo) |
| B3 retry window 8s > outer `asyncio.timeout(4)` | VIOLATION | n/a | n/a | n/a | miss | miss | miss |
| B4 gunicorn graceful 60s > grace 30s | VIOLATION | miss | miss | n/a | n/a | n/a | miss |
| B5 readiness `/readyz`, app serves `/ready` | CONDITIONAL (VIOLATION once accepted) | miss | miss: probes present | n/a | n/a | n/a | miss |
| `requests.get` without timeout (pricing.py:7) | delegated | n/a | n/a | n/a | hit S113 | hit B113 | hit `use-timeout` |

## Traps (should not be reported as reliability defects)

| Trap | FifthNine expected | Incumbent behaviour |
|---|---|---|
| `httpx.get` without timeout (bounded by the 5s default) | no finding | Ruff and Bandit silent: correct |
| Inactive `application-dev.yml` exposing actuator | no finding | Semgrep flags it (no profile model) |
| HPA cpu `AverageValue` with no cpu request (sample B) | no finding | kube-linter `unset-cpu-requirements` and Checkov CKV_K8S_10 flag the container. Valid hygiene, not an autoscaling defect |
| Dockerfile `--reload` replaced by Deployment `command` | no finding | nothing flagged: correct |
| PDB `maxUnavailable: 1` with default 25% rollout | no finding | nothing flagged |

## What this changes in the M0 proposal

1. FN-RES-001 Python is fully delegated. The M0 proposal said FifthNine would own `httpx` with `timeout=None`. Ruff 0.17.1 S113 and Bandit 1.9.4 B113 both already flag it. The proposal also said, from the Ruff docs page, that S113 flags `httpx` calls that omit `timeout`; Ruff 0.17.1 did not flag `httpx.get(url)` in sample B. Both statements are withdrawn. FN-RES-001 v0.1 covers Java clients only.
2. On these two samples, every planted item that needs two artifacts (A4, A5, A9, B4, B5) was missed by all six tools at the pinned versions. Single-file items were caught (Python timeouts, actuator YAML) or got adjacent hits (cpu requests). This is an observation about planted defects in CTO-authored samples. It is not a measurement of FifthNine against these tools on real repositories, and must not be quoted as one.
3. Semgrep's actuator rule is a security audit rule with no profile resolution. FN-CFG-001 keeps its place only for the profile-resolved case; we should say so plainly in docs rather than claim the check is new.
4. Checkov's 16 to 18 Kubernetes failures per sample are security posture checks; none concerns rollout, shutdown, probe correctness or autoscaling. The tools answer different questions, which is why the docs recommend running them together (CLI.md).

## Limits of this comparison

- Two CTO-authored samples. This shows coverage differences on planted items. It says nothing about precision or recall on real repositories; that is the M5 benchmark's job.
- Semgrep registry and Pro rules were not reachable. A registry rule for tenacity or Spring shutdown may exist; this must be re-checked from a machine with registry access before M3 freezes the overlap table.
- The frontier-LLM baseline required by contract section 8 is an M5 activity and was not run.
