# FifthNine Scan: M0 audit and engineering proposal

Date: 2026-10-21
Author: Claude (CTO)
Status: Reviewed by CEO 2026-10-21 (approved with conditions). Superseded in part: see DECISIONS.md errata ER-1 to ER-4 and the M0 approval package in docs/fifthnine/m0/.
Original status: Proposal for CEO decision. Nothing below is implemented. No code written beyond this document and a copy of the contract.

---

## A. Repository audit

### What exists

There is no FifthNine repository. I checked:

- The session workspace: empty, not a git repository.
- Every GitHub repository visible to this session (32 repos across `ramkumar1308`, `SasquatchLabsInc`, `mickymultani`). None is named or scoped as FifthNine, Incident Genome, or a scanner.
- The two personal repos whose names suggested overlap, cloned and read:
  - `ramkumar1308/deployment-validation-bot`: a post-deploy shell script plus three small Python validators (URL, DB, log scan). 7 Python lines in total. It runs against live endpoints, which Phase 1 forbids.
  - `ramkumar1308/sre-retry-backoff-wrapper`: a 23-line retry/backoff helper and example. It is a runtime library, not static analysis.

I did not open the `SasquatchLabsInc` repositories. FifthNine is a separate venture and its code should not draw from or sit beside Sasquatch code.

### What to preserve

Nothing as code. The prior FifthNine research (Failure Graph, journey verification, reliability contracts) is reference only, per contract section 0. I have no access to the 36 held-out and 8 journey cases and will not request it.

### Consequence

Phase 1 starts from an empty repository. The contract's package layout (section 10) is adopted as the starting point with the deltas in section B. I have staged the contract at `docs/fifthnine/PHASE1_IMPLEMENTATION_CONTRACT.md` in a local project folder, ready to commit once the repository exists (decision E1).

---

## B. Technical architecture and supported stacks

### B1. Design position

The moat is cross-artifact correlation. kube-linter, Checkov, Hadolint, Ruff and Semgrep each read one file type well. None of them knows that `application-prod.yml` sets `management.server.port: 8081` while the Deployment's readiness probe hits `8080/actuator/health/readiness`, or that a Spring Boot 3.4 graceful shutdown budget plus a `preStop` sleep exceeds `terminationGracePeriodSeconds`. Every rule I propose for the first wave either needs facts from two or more artifacts, or needs framework-version semantics the incumbents do not model. Single-file checks the incumbents already do well are delegated (section C3).

### B2. Pipeline

```
discover  -> walk repo, apply ignores, size caps, symlink containment; git diff if --base-ref
detect    -> classify files: pom.xml, build.gradle(.kts), pyproject/requirements/lockfiles,
             application*.yml|properties, *.java, *.py, Dockerfile*, k8s YAML
extract   -> per-format extractors emit Facts (no rule logic in extractors)
resolve   -> build the effective-config model:
             - Spring profile resolution (subset, see B4)
             - k8s object graph (Deployment <-> Service <-> Ingress <-> HPA <-> PDB by selector/name)
             - service linkage (Dockerfile <-> app module <-> k8s container), see B5
             - framework version + version-specific defaults table
evaluate  -> registered rules read resolved Facts, emit Findings with verdict + evidence
verify    -> dedupe, fingerprint, baseline/suppression status, coverage notices
report    -> console, JSON (versioned schema), SARIF 2.1.0; JUnit later
```

### B3. Fact model

Each fact carries a `status` that rules must respect:

| status | meaning | can support a blocking VIOLATION |
|---|---|---|
| `declared` | literal value in an active source | yes |
| `default` | framework default for a detected version, cited to vendor docs | yes, only when the version is resolved |
| `overridden` | declared, but superseded by a higher-precedence source | no (the winning fact is used) |
| `inferred` | derived by heuristic (e.g. single-service linkage) | no, CONDITIONAL at most |
| `unresolved` | placeholder (`${DB_URL}`), external ConfigMap, unknown profile, unknown version | no, produces UNKNOWN |

This is the mechanism behind "unknown is never passed as safe."

### B4. Spring config resolution: supported subset

Supported: `application.{yml,yaml,properties}`, `application-{profile}.*`, multi-document YAML with `spring.config.activate.on-profile`, active profile from `--profile`, from `.fifthnine.yml`, or from `SPRING_PROFILES_ACTIVE` in an in-repo k8s manifest or Dockerfile `ENV`. Env-var relaxed binding for keys set in manifests (`SERVER_PORT` -> `server.port`).

Not supported, reported as UNKNOWN: `spring.config.import`, Spring Cloud Config, Vault, ConfigMaps or Secrets not present in the repo, `@ConfigurationProperties` defaults set in Java code, profile groups. Any rule whose required key passes through one of these returns UNKNOWN with the reason.

### B5. Service linkage

k8s manifests reference images (`ghcr.io/acme/orders:1.4`), not Dockerfiles. There is no deterministic link. Proposal:

- Single-service repo (one Dockerfile, one app module, one workload): linkage is `inferred`. Findings that depend on it are capped at CONDITIONAL.
- Explicit mapping in `.fifthnine.yml` (`services: [{name, dockerfile, module, workload}]`): linkage is `declared`. Findings can be VIOLATION and blocking.
- Anything else: UNKNOWN with a coverage notice explaining how to declare the mapping.

This keeps cross-artifact rules from blocking on a guess. It needs CEO approval (E4) because it means most cross-artifact findings ship as CONDITIONAL until a team adds a five-line config.

### B6. Supported stacks for Phase 1

| Input | Supported | Explicitly not supported (coverage notice) |
|---|---|---|
| Java | Java 17 and 21 source; Spring Boot 3.2 through 3.5; Maven (`spring-boot-starter-parent` or BOM) and Gradle with literal plugin version | Boot 2.x, version catalogs with indirection (version -> UNKNOWN), Kotlin source, Quarkus, Micronaut |
| Java HTTP and resilience | `RestTemplate`/`RestTemplateBuilder`, `RestClient`, `WebClient`, Resilience4j via YAML, Spring Retry `@Retryable` | Feign, Apache HttpClient direct use, OkHttp (Phase 1.5 candidates) |
| Python | 3.10 to 3.13 source; FastAPI; `requests` 2.x; `httpx` 0.2x; `tenacity` 8.x and 9.x; uvicorn and gunicorn entrypoints | Django, Flask, aiohttp, Celery |
| Containers | Dockerfile (all instructions parsed, multi-stage aware) | Buildpacks, Jib config, Bazel images |
| Kubernetes | Plain YAML: `apps/v1` Deployment, `v1` Service, `networking.k8s.io/v1` Ingress, `autoscaling/v2` HPA, `policy/v1` PDB | Helm templates and Kustomize overlays are not rendered. User may scan `helm template` output (documented). StatefulSet, DaemonSet, Gateway API, Argo Rollouts later |
| Config | YAML, `.properties`, `.env` | HOCON, TOML app config |

### B7. Implementation choices

- Python 3.10+ CLI, `typer`, Pydantic v2 schemas, `pytest`, Ruff, mypy strict on `core/`.
- Java parsing: `tree-sitter` with `tree-sitter-java` wheels. Offline, no JVM, line-accurate. No regex for call-site semantics.
- Python parsing: stdlib `ast`.
- YAML: `ruamel.yaml` (line and column per node), with an alias-expansion cap and per-file size cap.
- Dockerfile: small in-house parser over the documented grammar; I will compare against `dockerfile-parse` (BSD) during M2 and pick on line-mapping accuracy.
- Rules: Python classes registered through an entry-point group `fifthnine.rules`, so third-party rule packs install as normal packages. Declarative YAML rules are deferred; the first-wave verdicts need arithmetic over resolved facts that a YAML DSL would obscure.
- Every rule ships `tests/fixtures/<RULE_ID>/{violation,satisfied,conditional,unknown}/`, the Checkov pass/fail discipline extended to four verdicts.
- Fingerprints: rule ID + subject key + normalized evidence values, not line numbers, so baselines survive unrelated edits.
- SARIF mapping: VIOLATION -> `error` or `warning` by severity; CONDITIONAL -> `note`; UNKNOWN and coverage notices go to `invocations[].toolExecutionNotifications`, not `results`, so GitHub code scanning is not flooded with unassessable items.
- Diff mode: parse the full repo at HEAD and at `--base-ref` (base read via `git show`, never checked out or executed), evaluate both, classify each finding as new, existing or fixed by fingerprint. Context always comes from the whole repo; the diff only filters what is reported as new.

### B8. Deltas from the contract

1. Package layout as section 10, plus `core/resolve/` (spring.py, k8s_graph.py, linkage.py, defaults/) because resolution is the product and deserves its own module.
2. Version-defaults table as data (`defaults/spring_boot.yaml`) with a docs URL per entry, reviewed per release.
3. JUnit output moved to M5 or later. CLI, JSON, SARIF in M1 to M4 as the contract prioritizes.
4. FN-DATA rules removed from Phase 1 (decision E2).

---

## C. Initial high-confidence rules and scanner comparison

### C1. First wave: 10 rules for M3

Ordered by differentiation. "Overlap" compares against the incumbents as checked today; every row gets re-verified against pinned tool versions in M0 before the rule is frozen.

| # | ID | Check | Artifacts correlated | Max verdict | Overlap |
|---|---|---|---|---|---|
| 1 | FN-DEP-003 | Shutdown budget exceeds pod grace period. Spring: `spring.lifecycle.timeout-per-shutdown-phase` (default 30s) when graceful shutdown is active (default from Boot 3.4; opt-in before). Plus any `preStop` sleep. Compared with `terminationGracePeriodSeconds` (k8s default 30s). Python: gunicorn `--graceful-timeout`, uvicorn `--timeout-graceful-shutdown` from Dockerfile CMD or k8s args | Spring config, Boot version, Dockerfile, Deployment | VIOLATION | None found. kube-linter has no termination budget check |
| 2 | FN-DEP-002 | Probe target does not match the resolved app endpoint: port vs `server.port` / `management.server.port`; path vs `management.endpoints.web.base-path` and whether health probes groups are enabled; FastAPI route existence for the probe path; uvicorn `--port` | Spring config + profile, Python AST, Dockerfile, Deployment | VIOLATION | Partial. kube-linter `liveness-port`/`readiness-port`/`startup-port` check only that the probe port is a declared containerPort. None check against application config |
| 3 | FN-CAP-002a | HPA scales on CPU or memory utilization while a container in the target pod has no request for that resource, so the HPA cannot compute utilization | HPA + Deployment | VIOLATION | Partial. kube-linter `unset-cpu-requirements` and Checkov flag missing requests generically; neither ties it to a broken HPA. FifthNine reports the consequence and raises severity |
| 4 | FN-CAP-002b | Deployment sets `spec.replicas` while an HPA targets it, so each apply resets replica count (k8s HPA docs recommend removing the field) | HPA + Deployment | CONDITIONAL (GitOps tools may ignore the diff) | None found |
| 5 | FN-DEP-001 | RollingUpdate `maxUnavailable` (absolute or %, rounded per k8s rules) lets ready replicas fall below the PDB `minAvailable`. PDBs gate evictions, not rollouts, so the declared floor is not enforced during deploys | Deployment + PDB | VIOLATION | None found. kube-linter's PDB checks cover PDBs that block all disruption, a different failure |
| 6 | FN-RES-004 | `tenacity` `@retry` or `Retrying` with no `stop=` argument retries forever (tenacity default is `stop_never`), on a call path that reaches an HTTP client | Python AST | VIOLATION | None found in Ruff or Bandit. Semgrep registry to be checked in M0 |
| 7 | FN-RES-002 | Retry window exceeds a known deadline: Resilience4j `maxAttempts` x (`waitDuration` with exponential multiplier) plus per-attempt timeout, vs `timelimiter` `timeoutDuration` on the same instance; tenacity `stop_after_attempt` x wait vs explicit `stop_after_delay` or client timeout | Resilience4j YAML, Python AST | VIOLATION when both bounds are declared; else CONDITIONAL | None found |
| 8 | FN-RES-001 | HTTP client with no effective timeout. Java: `RestTemplate`/`RestClient`/`WebClient` built without connect/read timeouts where the resolved Boot version default is unbounded. Python: `httpx` with explicit `timeout=None` | Java AST, Boot version, Python AST | VIOLATION (Java with resolved version); CONDITIONAL otherwise | Python `requests` without timeout is already covered by Ruff S113 and Bandit B113: delegated. Ruff's docs say S113 also flags `httpx` calls that omit `timeout`, though `httpx` defaults to 5s. FifthNine does not flag the omitted case. That is a precision difference to verify and benchmark |
| 9 | FN-CFG-001 | Effective production profile enables a risky setting: actuator `exposure.include: "*"`, `server.error.include-stacktrace: always`, uvicorn `--reload` or FastAPI `debug=True` in the production image entrypoint | Spring config + profile, Dockerfile, Python AST | VIOLATION when profile resolved; UNKNOWN when not | Partial. Semgrep has Spring actuator exposure rules on single files. FifthNine adds profile resolution: the same key in `application-dev.yml` is not a finding |
| 10 | FN-NET-001 | Actuator or management port reachable from outside: management port or actuator path exposed through a `LoadBalancer`/`NodePort` Service or an Ingress path | Spring config, Service, Ingress | VIOLATION | Partial. kube-linter `exposed-services` flags Service types generically; it has no notion of which port is administrative |

Engine features (not counted as rules): FN-OPS-001 coverage notices per domain, FN-OPS-002 UNKNOWN for unresolved profiles, missing referenced files, unrendered templates.

### C2. Expansion candidates after the M3 gate (toward 15)

- FN-RES-006 retry with no backoff (Spring Retry `@Backoff` absent, Resilience4j without wait), CONDITIONAL.
- FN-RES-003 retries on POST/PATCH without idempotency evidence, CONDITIONAL only.
- FN-CAP-003 Hikari `maximumPoolSize` x HPA `maxReplicas`, reported as a derived figure; CONDITIONAL because DB connection limits are rarely in-repo.
- FN-DEP-004 Dockerfile shell-form `CMD`/`ENTRYPOINT` combined with a declared graceful-shutdown config: signal may not reach the app. CONDITIONAL, because whether `sh -c` forwards SIGTERM depends on the shell. Hadolint DL3025 flags the form; FifthNine adds the reliability consequence only when graceful shutdown is configured.
- FN-RES-005 multi-layer retry amplification where both layers are explicit.

### C3. Delegated to existing tools in Phase 1

| Contract candidate | Delegate to | Reason |
|---|---|---|
| FN-CAP-001 missing requests/limits | kube-linter `unset-cpu-requirements`, `unset-memory-requirements`; Checkov CKV_K8S_10 to 13 | Mature, enabled by default in kube-linter |
| Python `requests` without timeout | Ruff S113, Bandit B113 | Mature |
| Missing liveness/readiness probes | kube-linter, Checkov CKV_K8S_8/9 | Presence checks are absence warnings, which the contract tells us to avoid |
| FN-NET-002 generic broad ingress | Checkov, kube-linter `exposed-services` | Folded into FN-NET-001 for the admin-port case |
| FN-DATA-001/002 migrations | Squawk, Atlas lint | Specialist tools exist, and the contract rules out a migration linter |
| FN-CFG-002 dev fallback endpoints | Deferred | Needs a reliable notion of "dev endpoint"; high false-positive risk |
| Secrets in config | gitleaks, Trivy, Checkov secrets | Security scope, not ours |

Phase 1 ships a published mapping table (`docs/coverage.md`) and a README section showing how to run FifthNine next to these tools. Importing their SARIF into one report is an M4 stretch, not a commitment.

### C4. Why these rules should pass the "useful to an experienced SRE" test

Each first-wave rule describes a failure that shows up as an incident, not a hygiene note: pods SIGKILLed mid-drain on every deploy (1), healthy pods marked unready or restarted forever (2), an autoscaler that silently never scales (3), replica count snapping back on apply (4), a deploy that drops below a declared capacity floor (5), a retry loop that never ends (6), retries that cannot finish inside the deadline (7), threads hung on a dead dependency (8), heap dumps served from production (9, 10). Every one is decidable from repo artifacts when the inputs are declared.

---

## D. Milestone acceptance criteria and risks

### D1. Acceptance matrix

| Milestone | Gate criteria (all must hold) | Evidence I will return |
|---|---|---|
| M0 Audit and contracts | CEO decisions E1 to E8 recorded; JSON result schema v1 and Fact/Finding Pydantic models frozen; CLI surface frozen; overlap table verified against pinned versions (kube-linter, Checkov, Ruff, Bandit, Semgrep registry, Hadolint); licenses of all dependencies recorded | Schema files, mocked console + JSON output on one fixture, labeled as mocked |
| M1 CLI skeleton | `pipx install .` works on clean Linux and macOS; `fifthnine scan .` on 3 repos returns inventory, coverage and status INCOMPLETE where nothing is assessable (never PASS on an empty rule set); two runs produce byte-identical JSON; network-disabled test passes (sockets blocked in pytest); exit codes 0/1/2 tested | CLI transcripts, test counts, determinism diff |
| M2 Extractors and resolution | Facts with exact file:line on two sample services (one Spring, one FastAPI, each with Dockerfile + k8s); 100% of facts in fixtures map to correct lines (fixture-asserted); unresolved placeholders, unknown profile and missing ConfigMap yield `unresolved`; conflicting profiles yield UNKNOWN; untrusted-input tests (symlink escape, 50 MB file, YAML alias bomb) pass | Fact dump for both services, failing-input test list |
| M3 Rule pack | 10 rules, each with 4 verdict fixtures and at least one false-positive trap fixture; rule docs with references; zero absence-only VIOLATIONs; CEO plus one outside SRE review every finding on the two sample services and on two unfamiliar public repos | Per-rule fixture results, review sheet with accept/reject per finding |
| M4 CI and policy | `--base-ref` demo: new timeout defect reported as new, pre-existing finding as existing; SARIF accepted by GitHub code scanning upload; baseline create/apply; suppression with expiry, expired suppressions reported as expired and re-activated; report-only default exits 0; opt-in gate exits 1; network egress test in CI | PR link with code-scanning annotations, CI logs |
| M5 Validation and alpha | Independent benchmark run with per-rule precision and recall plus Wilson intervals; comparison against kube-linter, Checkov, Ruff, Semgrep and a frontier LLM given identical files; onboarding timed with 5 practitioners; docs state FifthNine does not certify production readiness; threat model, SBOM, license review | Benchmark report with failures listed, practitioner notes |

### D2. Statistical honesty on the precision gate

With zero false positives, a 95% Wilson lower bound reaches 0.90 only at 35 confirmed findings. At 20 findings with zero false positives, the lower bound is 0.84; at 20 with one false positive, 0.76. The benchmark must contain at least 35 in-scope true-positive opportunities across the rule pack before "at least 90% precision" can be claimed as a bound. Below that I will report the point estimate, the interval and N, and call the gate unmet.

### D3. Risks and tradeoffs

1. Helm and Kustomize are common in real repos. Without rendering, many real repos will produce mostly UNKNOWN on the k8s side. Mitigation: document `helm template` / `kustomize build` piping; measure the UNKNOWN rate on the unfamiliar repos and report it. Rendering in a sandbox is a Phase 1.5 decision.
2. Service linkage (B5) caps most cross-artifact findings at CONDITIONAL in multi-service repos. This trades early impressiveness for trust. I recommend accepting it.
3. Spring config resolution is a deep surface. The subset in B4 covers common layouts. Misresolving a profile is the most likely source of false VIOLATIONs, so profile resolution gets the heaviest fixture coverage.
4. Version-dependent defaults change between minor releases (graceful shutdown default changed in Boot 3.4). Every default needs a cited source and a version range, or the rule degrades to CONDITIONAL.
5. "kube-linter already does this" perception. Rules 2, 3 and 10 sit next to existing checks. The README must show side-by-side output on the same repo, where FifthNine catches the mismatch and the incumbent does not.
6. 10 rules can look thin next to Checkov's catalog. The contract prefers precision. I agree; the comparison benchmark is the answer, not rule count.
7. Benchmark independence. No independent scenario author is named. Without one, M5 cannot pass. I also cannot serve as the independent SRE reviewer of my own rules, and neither can the founder alone.
8. CTO continuity. My context does not persist across sessions. The contract file plus a `docs/fifthnine/DECISIONS.md` log in the repo are the source of truth; I will reread them at each session start.
9. Schedule. Six weeks assumes M2 resolution goes to plan. Spring resolution is the item most likely to slip; if it does, I will stop and report rather than cut fixture coverage.

---

## E. Decisions requiring CEO approval

| # | Decision | My recommendation |
|---|---|---|
| E1 | Repository home and license. A new public repo is needed and this session needs push access to it | New GitHub org or personal repo named `fifthnine`, not under `SasquatchLabsInc`. Apache-2.0 (same family as Checkov, permissive for adoption and acquisition paths) |
| E2 | Remove the data/integration compatibility domain (FN-DATA-001/002) from Phase 1 | Remove; point to Squawk and Atlas in docs |
| E3 | Helm and Kustomize: no rendering in Phase 1; users may scan rendered output | Accept |
| E4 | Service linkage rule: blocking only with declared linkage, inferred linkage capped at CONDITIONAL | Accept |
| E5 | Supported versions: Spring Boot 3.2 to 3.5, Java 17/21, Python 3.10 to 3.13; Boot 2.x reported as unsupported | Accept |
| E6 | First-wave rule list C1 and delegation list C3 | Approve as the M3 target, subject to M0 overlap re-verification |
| E7 | Independent benchmark author and at least one outside SRE reviewer | Name both before M3 ends; M5 cannot pass otherwise |
| E8 | SARIF import of Ruff/kube-linter/Checkov results into one report | M4 stretch, not a commitment |

On approval I start M0: freeze the schemas, verify the overlap table against pinned tool versions, select the two sample services and the two unfamiliar public repos (candidates: `spring-projects/spring-petclinic` and a FastAPI service with in-repo manifests, licenses to be checked), and return the mocked demo for the M0 gate.
