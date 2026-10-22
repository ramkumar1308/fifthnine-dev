# First-wave rule candidates: applicability, semantics and overlap evidence

Status: M0 technical review (CEO condition 7), updated for schema revision 2. Every rule here is a candidate (E6). None is frozen until its M3 fixtures pass and the outside SRE reviewer has seen its findings. Under D5 (approved provisionally) no rule is blocking-capable until its runtime and boundary-case tests pass.

How to read the expected verdicts in the matrices below:

- They assume the component versions involved are `tested` in `src/fifthnine/data/support_matrix.v1.yaml`. Today none is. Until a version is tested, every failing row is CONDITIONAL with an `untested_version` assumption (D9).
- D2 (revised by the CEO): a failing check whose material input may be replaced from outside the repository is CONDITIONAL, with the repository-derived computation kept and the assumption named. It is UNKNOWN when the input has no value in the repository at all. It becomes VIOLATION only when the team declares the assumption in `.fifthnine.yml`; the finding then cites that declaration.

## Pinned versions reviewed

| Component | Versions reviewed | How |
|---|---|---|
| Spring Boot | v3.2.12, v3.3.13, v3.4.13, v3.5.16 (latest patch of each minor on 2026-10-21) | Tagged source from github.com/spring-projects/spring-boot |
| Spring Framework | v6.1.15 (Boot 3.2), v6.2.15 (Boot 3.4), v6.2.19 (Boot 3.5) | Tagged source; mapping from each Boot tag's `gradle.properties` |
| Resilience4j | v2.4.0 | Tagged source (`resilience4j-spring6`) |
| Apache HttpClient 5 | rel/v5.4.4, rel/v5.5; httpcore5 rel/v5.3.4 | Tagged source |
| Kubernetes | kubernetes/website `main` on 2026-10-21 | Docs source |
| Python libraries | tenacity 9.2.1 and 8.2.3, httpx 0.28.1, requests 2.34.2, uvicorn 0.54.0, gunicorn 26.2.0, fastapi 0.159.1 | Installed from PyPI and read; behaviour confirmed by runtime check where noted |
| Incumbents | see INCUMBENT_RUNS.md | Executed |

Paths below abbreviate `spring-boot-project/spring-boot-autoconfigure/src/main/java/org/springframework/boot/autoconfigure/` as `SB-AC/`, the actuator equivalent as `SB-ACT-AC/`, and `spring-boot-project/spring-boot/src/main/java/org/springframework/boot/` as `SB/`.

## Outcome of the review

| # | Rule | M0 outcome | What changed |
|---|---|---|---|
| 1 | FN-DEP-003 shutdown budget vs grace period | Keep, refined | Severity depends on whether the budget was declared or defaulted. Lower-bound reasoning when preStop duration is unknown. Immediate shutdown and uvicorn's unbounded default are not applicable |
| 2 | FN-DEP-002 probe target vs resolved endpoint | Keep | Path algorithm fixed from source, including a ManagementPortType edge case and the 3.4 `access` model |
| 3 | FN-CAP-002a HPA utilization without requests | Keep, narrowed | Utilization targets only. `AverageValue` and `ContainerResource` handled separately |
| 4 | FN-CAP-002b `spec.replicas` with HPA | Keep as advisory-only | Never VIOLATION, never blocking |
| 5 | FN-DEP-001 rollout vs PDB | Keep, narrowed | Only PDB `minAvailable` counts as a declared floor. `maxUnavailable` PDBs excluded after the sample B trap |
| 6 | FN-RES-004 unbounded tenacity retry | Keep, refined | VIOLATION only on a request path; background and startup paths are CONDITIONAL |
| 7 | FN-RES-002 retry window vs deadline | Modified | The M0 proposal compared Resilience4j retry against TimeLimiter. Under default aspect order the TimeLimiter is per attempt, so that comparison was wrong. VIOLATION now needs an explicit outer deadline |
| 8 | FN-RES-001 no effective timeout | Narrowed to Java | Python cases are covered by Ruff S113 and Bandit B113 (verified by running them) |
| 9 | FN-CFG-001 risky setting in effective prod profile | Keep | FifthNine's addition is profile resolution only; Semgrep already flags the YAML key |
| 10 | FN-NET-001 management surface reachable from outside | Keep, extended | Adds the SAME-port case where an Ingress on `/` publishes `/actuator` |

Candidates added for later waves from this review: FN-DEP-005 exec preStop hook in an image without a shell (Spring's own docs warn that the container needs a shell), FN-RES-003 non-idempotent retry (observed in sample A).

---

## FN-DEP-003 Shutdown budget exceeds pod grace period

### Semantics verified

| Fact | Value | Source |
|---|---|---|
| Spring graceful shutdown default | `IMMEDIATE` in 3.2 and 3.3; `GRACEFUL` in 3.4 and 3.5 | `SB-AC/web/ServerProperties.java`: v3.2.12 line 114, v3.3.13 line 116, v3.4.13 line 117, v3.5.16 line 116 |
| `spring.lifecycle.timeout-per-shutdown-phase` default | 30s, all four versions | `SB-AC/context/LifecycleProperties.java` line 36 |
| Timeout applies per SmartLifecycle phase | Total can exceed one timeout when several phases have work | Property name and LifecycleProperties javadoc; FifthNine uses one phase as a lower bound |
| k8s grace period default | 30s | k8s pod-lifecycle docs |
| preStop runs inside the grace period | yes; one-off 2s extension if still running | k8s pod-lifecycle docs ("If the preStop hook is still running after the grace period expires, the kubelet requests a small, one-off grace period extension of 2 seconds") |
| Spring guidance | Use a preStop sleep; raise `terminationGracePeriodSeconds` if shutdown can exceed 30s; exec preStop needs a shell in the image | Spring Boot 3.5 how-to `deployment/cloud.adoc`, section Kubernetes Container Lifecycle |
| preStop `sleep` action | Spring docs cite Kubernetes 1.32+ for the native `sleep` handler; otherwise `exec` with `sh -c sleep N` | same |
| gunicorn | `graceful_timeout` default 30; CLI flags applied after `GUNICORN_CMD_ARGS` and config file | gunicorn 26.2.0 `config.py` GracefulTimeout; `app/base.py` load_config |
| uvicorn | `timeout_graceful_shutdown` default `None`: waits for tasks with no limit | uvicorn 0.54.0 `server.py` shutdown(), `config.py` |

### Verdict logic

`budget = preStop_seconds + drain_seconds`. VIOLATION when `budget > grace` and every input is established. If preStop duration cannot be parsed, use 0 as a lower bound: still VIOLATION if `drain > grace`, otherwise UNKNOWN. Not applicable when drain is immediate or unbounded by default (uvicorn without the flag): the grace period is then the only bound, which is the platform working as designed.

Severity: medium when the drain budget or preStop is declared in the repo; low when every input is a default (Boot 3.4+ default 30s + Spring-recommended preStop + default 30s grace), because nobody stated an intent that the platform breaks.

### Version test matrix (Spring)

Unless stated: preStop `exec sh -c "sleep 10"`, image has `/bin/sh`, grace unset (30s), linkage declared, no external env source.

| # | Boot | `server.shutdown` | phase timeout | Other | Expected |
|---|---|---|---|---|---|
| S1 | 3.3 | unset | unset | | not applicable (IMMEDIATE) |
| S2 | 3.4 | unset | unset | | VIOLATION low (10 + 30 > 30) |
| S3 | 3.4 | unset | 45s | | VIOLATION medium (sample A) |
| S4 | 3.4 | unset | 15s | | SATISFIED (25 <= 30) |
| S5 | 3.2 | graceful | 45s | | VIOLATION medium |
| S6 | 3.5 | immediate | 45s | | not applicable |
| S7 | 3.4 | unset | 45s | grace 60 | SATISFIED |
| S8 | 3.4 | unset | 25s | preStop `sh -c /hooks/drain.sh` | UNKNOWN (preStop unparsed, 0 + 25 <= 30) |
| S9 | 3.4 | unset | 45s | preStop `sh -c /hooks/drain.sh` | VIOLATION medium (lower bound 45 > 30) |
| S10 | 3.4 | unset | 25s | preStop native `sleep: {seconds: 10}` | VIOLATION medium (35 > 30) |
| S11 | unresolved (Gradle catalog) | unset | 45s | | UNKNOWN (graceful depends on version) |
| S12 | 3.4 | unset | 45s | Deployment env `SERVER_SHUTDOWN=immediate` | not applicable (env beats application.yml) |
| S13 | 3.4 | unset | 45s | Deployment args `--spring.lifecycle.timeout-per-shutdown-phase=15s` | SATISFIED (command line has highest precedence) |
| S14 | 3.4 | unset | 45s | `JAVA_TOOL_OPTIONS=-Dspring.lifecycle.timeout-per-shutdown-phase=15s` in Deployment | SATISFIED (system property beats config file) |
| S15 | 3.4 | unset | 45s | `JAVA_OPTS=...` with exec-form `java -jar` entrypoint | VIOLATION; `JAVA_OPTS` is not read by the JVM and is ignored with a note |
| S16 | 3.4 | unset | 45s | external `envFrom` Secret | CONDITIONAL (`external_override_env`), computation kept; VIOLATION when `external_overrides.mode: none` is declared |
| S17 | 3.4 | unset | 45s | external ConfigMap mounted at `<WORKDIR>/config` | CONDITIONAL (`external_override_file`): the repository value exists but Spring reads `file:./config/` first (`SB/context/config/ConfigDataEnvironment.java` default search locations) |
| S18 | 3.4 | unset | 45s | linkage inferred | CONDITIONAL |
| S19 | 3.4 | unset | 45s | distroless runtime image, exec preStop | CONDITIONAL; preStop cannot run, budget recomputed as 45 > 30; FN-DEP-005 candidate |

### Python matrix

| # | Server and flags | grace | preStop | Expected |
|---|---|---|---|---|
| P1 | gunicorn `--graceful-timeout=60` in Deployment args | 30 | none | VIOLATION medium (sample B). No override exposure: the CLI flag is applied after environment settings. Blocking-capable once the rule is promoted (D5) |
| P2 | gunicorn, no flag | 30 | sleep 5 | VIOLATION low (5 + 30 default > 30) |
| P3 | gunicorn, no flag, `gunicorn.conf.py` in WORKDIR with literal `graceful_timeout = 20` | 30 | none | SATISFIED |
| P4 | gunicorn, no flag, `gunicorn.conf.py` with computed value | 30 | none | UNKNOWN |
| P5 | uvicorn, no `--timeout-graceful-shutdown` | 30 | any | not applicable |
| P6 | uvicorn `--timeout-graceful-shutdown 40` | 30 | none | VIOLATION medium |
| P7 | command only in Dockerfile CMD | any | | CONDITIONAL until linkage declared |

### Overlap

kube-linter 0.8.3, Checkov 3.3.30, Hadolint 2.15.1 and the Semgrep subset reported nothing for A4 or B4. No incumbent check compares app shutdown settings with the pod spec.

---

## FN-DEP-002 Probe target does not match the resolved application endpoint

### Semantics verified (Spring)

| Fact | Source |
|---|---|
| Probe groups `liveness` and `readiness` are created when `management.endpoint.health.probes.enabled` is true, or when it is unset and the app runs on Kubernetes (detected from `KUBERNETES_SERVICE_HOST` and `KUBERNETES_SERVICE_PORT`). 3.4 (v3.4.13 line 94) and 3.5 also auto-enable on Cloud Foundry; corrected in M2-0, 3.2 and 3.3 not re-checked | `SB-ACT-AC/availability/AvailabilityProbesAutoConfiguration.java` ProbesCondition (v3.2.12, v3.5.16); `SB/cloud/CloudPlatform.java` KUBERNETES |
| Additional paths `/livez` and `/readyz` on the main server port only when `management.endpoint.health.probes.add-additional-paths=true` | `SB-ACT-AC/availability/AvailabilityProbesHealthEndpointGroupsPostProcessor.java` lines 38-39; `AvailabilityProbesHealthEndpointGroups.java` |
| Management port type: DISABLED if `management.server.port < 0`; SAME if unset, or if it equals `server.port`, or if it is 8080 and `server.port` is unset; otherwise DIFFERENT | `SB-ACT-AC/web/server/ManagementPortType.java` get(), identical in v3.2.12 and v3.5.16 |
| `management.endpoints.web.base-path` default `/actuator` | `SB-ACT-AC/endpoint/web/WebEndpointProperties.java` line 47 |
| `management.server.base-path` default empty | `SB-ACT-AC/web/server/ManagementServerProperties.java` |
| Base path is relative to `server.servlet.context-path` (or `spring.webflux.base-path`) on the same port, and to `management.server.base-path` on a separate port | Spring Boot 3.5 reference `actuator/monitoring.adoc` |
| Default web exposure includes `health` | `WebEndpointAutoConfiguration` uses `EndpointExposure.WEB.getDefaultIncludes()` |
| Endpoint access model (`management.endpoint.<id>.access`) exists from 3.4; `enabled` before | `spring-boot-actuator/.../endpoint/Access.java` present at v3.4.13, absent at v3.3.13 |

Resolved probe URL for Spring:

```
port  = management.server.port if type == DIFFERENT else server.port (default 8080)
root  = management.server.base-path if DIFFERENT else server.servlet.context-path (or spring.webflux.base-path)
path  = root + management.endpoints.web.base-path + "/" + (path-mapping.health or "health") + "/" + group
also  = "/livez", "/readyz" on server.port under context-path, only if add-additional-paths
reach = health exposed (exposure include/exclude) and access not NONE (3.4+) / enabled (3.2-3.3)
```

One edge case is documented differently from the source: the docs say a configured `management.server.port` makes the base path relative to `management.server.base-path`, while `ManagementPortType` treats a port equal to `server.port` as SAME. FifthNine follows the source. A runtime fixture for this case is required in M2 before the rule can block on it.

### Semantics verified (FastAPI)

Routes are collected statically from `FastAPI(...)` construction, `@app.<method>`, `APIRouter(prefix=...)`, and `include_router(router, prefix=...)` with literal arguments. Dynamic registration (loops, conditionals, imported factories), `mount()` of non-FastAPI apps, and `root_path` produce UNKNOWN. Runtime check on sample B: `/ready` 200, `/readyz` 404.

### Version test matrix (Spring)

Probe is `httpGet` on the readiness group unless stated. Linkage declared.

| # | Config | Probe | Expected |
|---|---|---|---|
| R1 | all defaults | `8080 /actuator/health/readiness` | SATISFIED (auto-enabled on k8s) |
| R2 | `management.server.port: 8081` | `8080 /actuator/health/readiness` | VIOLATION high (sample A) |
| R3 | `management.server.port: 8081` | `8081 /actuator/health/readiness` | SATISFIED |
| R4 | `management.server.port: 8080`, `server.port` unset | `8080 /actuator/health/readiness` | SATISFIED (SAME by source rule) |
| R5 | `server.servlet.context-path: /api`, SAME | `/actuator/health/readiness` | VIOLATION (needs `/api/actuator/...`) |
| R6 | context-path `/api`, management 8081 | `8081 /actuator/health/readiness` | SATISFIED (context path not applied on separate port); runtime fixture required |
| R7 | `probes.enabled: false` | `/actuator/health/readiness` | VIOLATION (group absent) |
| R8 | management 8081, `add-additional-paths: true` | `8080 /readyz` | SATISFIED |
| R9 | `management.endpoints.web.base-path: /manage` | `/actuator/health/readiness` | VIOLATION |
| R10 | `management.endpoints.web.exposure.exclude: health` | `/actuator/health/readiness` | VIOLATION |
| R11 | 3.4+, `management.endpoint.health.access: none` | `/actuator/health/readiness` | VIOLATION |
| R12 | 3.3, `management.endpoint.health.access: none` | `/actuator/health/readiness` | SATISFIED; property unknown to 3.3, reported as ignored |
| R13 | `server.port: ${PORT:8080}`, Deployment env `PORT=9000` | `8080 ...` | VIOLATION (resolved 9000) |
| R14 | management port in `application-prod.yml`, profile prod active | port from prod file | evaluated against prod value |
| R15 | `tcpSocket` or `exec` probe | | not applicable |
| R16 | named probe port missing from containerPorts | | not FifthNine's (kube-linter `readiness-port`) |
| R17 | linkage inferred | R2 inputs | CONDITIONAL (sample A default mode) |

### Overlap

kube-linter `liveness-port`, `readiness-port`, `startup-port` (on by default) check that the probe port is a declared container port. In sample A, port 8080 is declared, so kube-linter passes the broken probe. Checkov CKV_K8S_8 and CKV_K8S_9 check probe presence and pass. FifthNine does not re-implement the containerPort check.

---

## FN-CAP-002a HPA utilization target with missing requests

Semantics: for a `Resource` metric with `target.type: Utilization`, if some of the pod's containers have no request for that resource, utilization is undefined and the HPA takes no action for that metric (k8s HPA docs, "if some of the Pod's containers do not have the relevant resource request set..."). `AverageValue` targets do not need requests. `ContainerResource` metrics need the request on the named container only.

| # | Case | Expected |
|---|---|---|
| H1 | cpu Utilization, no cpu request on any container | VIOLATION high (sample A) |
| H2 | cpu Utilization, request on app container, none on a sidecar | VIOLATION high |
| H3 | cpu AverageValue, no request | not applicable (sample B trap) |
| H4 | ContainerResource cpu on `app`, request only on `app` | SATISFIED |
| H5 | HPA target not found in repo | UNKNOWN (FN-OPS-002) |
| H6 | requests may be injected by a namespace LimitRange or webhook at admission | CONDITIONAL (`cluster_admission_default`) with the computation kept; VIOLATION when `platform.admission_defaults: none` is declared (sample A, A7) |

Overlap: kube-linter `unset-cpu-requirements` and Checkov CKV_K8S_10 flag the missing request on every workload, HPA or not. They do not state that autoscaling is broken, and they also fire on sample B where autoscaling works. FifthNine's finding is narrower and carries a different severity.

---

## FN-CAP-002b `spec.replicas` set on an HPA-targeted Deployment

k8s HPA docs, "Migrating Deployments and StatefulSets to horizontal autoscaling": with an HPA enabled, `spec.replicas` should be removed from manifests because applying the manifest resets the replica count. Always CONDITIONAL (Argo CD and Flux can be configured to ignore the field), advisory-only, low severity. No incumbent check found.

---

## FN-DEP-001 Rollout can drop below the PDB `minAvailable` floor

Semantics: PDBs gate voluntary evictions, not Deployment rolling updates ("workload resources (such as Deployment and StatefulSet) are not limited by PDBs when doing rolling upgrades", k8s disruptions docs). Rolling update `maxUnavailable` percentages round down, `maxSurge` percentages round up, default 25% each (k8s Deployment docs). PDB `minAvailable` percentages round up (k8s configure-pdb docs, "Rounding logic when specifying percentages").

Check: for each replica count n in the declared range (`spec.replicas`, or HPA `minReplicas..maxReplicas`), `n - unavailable(n) < pdb_min(n)`.

M0 change: a PDB with `maxUnavailable: 1` and a default 25% rollout would fire at n >= 8 on a large share of real repositories. That PDB shape usually expresses "drain one node at a time", not a capacity floor. Only `minAvailable` is treated as a declared floor in v0.1.

| # | Case | Expected |
|---|---|---|
| D1 | n 4..10, maxUnavailable 50%, minAvailable 75% | VIOLATION medium (sample A: n=4 gives 2 < 3) |
| D2 | n=3, default 25%, minAvailable 2 | SATISFIED (floor(0.75)=0 unavailable) |
| D3 | n=8, default 25%, minAvailable 75% | SATISFIED at the boundary: 8 - floor(2) = 6, ceil(6) = 6. Fixture asserts it |
| D4 | PDB maxUnavailable 1, any rollout | not applicable (sample B trap) |
| D5 | strategy Recreate with a minAvailable PDB | VIOLATION (all pods down during rollout) |
| D6 | PDB selector does not match the Deployment | not applicable |
| D7 | HPA present but `minReplicas` unset (defaults to 1) | evaluated from n=1 |

Overlap: kube-linter `pdb-min-available` and `pdb-max-unavailable` flag PDBs that block all disruptions, a different failure. No incumbent compares rollout settings with the PDB.

---

## FN-RES-004 Unbounded tenacity retry

Semantics: tenacity `retry`, `Retrying` and `AsyncRetrying` default to `stop=stop_never` (tenacity 9.2.1 `__init__.py` lines 262 and 758; same default in 8.2.3). Runtime check on sample B: 4 attempts in 12s and still retrying.

| # | Case | Expected |
|---|---|---|
| T1 | `@retry(...)` without `stop=`, reachable from a FastAPI route | VIOLATION high (sample B) |
| T2 | same, only reachable from a background task, startup hook or `__main__` worker loop | CONDITIONAL (reconnect-forever loops are often intended) |
| T3 | explicit `stop=stop_never` on a request path | VIOLATION medium |
| T4 | `stop=stop_after_attempt(n)` or `stop_after_delay(d)` | SATISFIED |
| T5 | stop passed through a variable defined elsewhere | UNKNOWN |
| T6 | bare `@retry` with no arguments | VIOLATION (also retries on every exception) |

Overlap: no rule in Ruff 0.17.1, Bandit 1.9.4 or semgrep-rules @16b9e6d. Registry rules not checked (registry unreachable).

---

## FN-RES-002 Retry window exceeds an explicit outer deadline

Semantics corrected at M0. Resilience4j Spring aspect order defaults: Retry `LOWEST_PRECEDENCE - 5`, CircuitBreaker `-4`, RateLimiter `-3`, TimeLimiter `-2`, Bulkhead `-1` (resilience4j v2.4.0 `*ConfigurationProperties`). Retry is outermost, so TimeLimiter bounds each attempt, not the whole retry. Retry defaults: 3 attempts, 500ms wait (`RetryConfig.java` lines 39-40). tenacity `stop_after_delay` checks elapsed time after an attempt and can exceed its limit by a wait plus a full attempt; `stop_before_delay` accounts for the upcoming sleep only (tenacity 9.2.1 `stop.py`).

VIOLATION requires a worst-case window computed from declared values and an explicit outer deadline in the repo: `asyncio.timeout(...)` or `asyncio.wait_for(...)` wrapping the retried call in the same call chain, or a Resilience4j TimeLimiter whose aspect order was configured outside Retry. Without an outer deadline, the window is reported as a derived fact, not a finding.

| # | Case | Expected |
|---|---|---|
| W1 | tenacity 3 attempts x 2s timeout + 2 x 1s wait = 8s inside `asyncio.timeout(4)` | VIOLATION medium (sample B; runtime: cancelled at 4.0s after 2 attempts) |
| W2 | same inside `asyncio.timeout(10)` | SATISFIED |
| W3 | outer timeout value from a pydantic-settings field, out-of-repo env source attached | CONDITIONAL (`external_override_env`), computation kept (sample B, B3) |
| W4 | Resilience4j retry + TimeLimiter, default aspect order | no finding (TimeLimiter is per attempt) |
| W5 | Resilience4j with `resilience4j.timelimiter.time-limiter-aspect-order` set below retry order | evaluated as outer deadline |
| W6 | per-attempt timeout unbounded | not this rule (FN-RES-001 or delegated) |

Overlap: none found.

---

## FN-RES-001 HTTP client with no effective timeout (Java only in v0.1)

| Fact | Source |
|---|---|
| `new RestTemplate()` uses `SimpleClientHttpRequestFactory` | spring-framework v6.1.15 and v6.2.19 `http/client/support/HttpAccessor.java` line 56 |
| `SimpleClientHttpRequestFactory` connect/read timeout default -1 (not applied; `HttpURLConnection` default 0 = infinite) | same tags, `SimpleClientHttpRequestFactory.java` lines 50, 52 |
| Static `RestClient.builder()` factory: HttpComponents > Jetty > (6.2: ReactorNetty) > JDK > Simple | `web/client/DefaultRestClientBuilder.java` initRequestFactory |
| Boot's injected builders: 3.2/3.3 detect HttpComponents > Jetty > OkHttp > Simple; 3.4/3.5 HttpComponents > Jetty > Reactor > JDK > Simple | `SB/web/client/ClientHttpRequestFactories.java` (3.2, 3.3); `SB/http/client/ClientHttpRequestFactoryBuilder.java` detect() (3.4, 3.5) |
| `spring.http.client.connect-timeout` / `read-timeout` exist from 3.4 | `SB-AC/http/client/HttpClientProperties.java` present at v3.4.13 and v3.5.16, absent at v3.2.12 and v3.3.13 |
| `spring.http.reactiveclient.*` exists in 3.5 | `SB-AC/http/client/reactive/HttpReactiveClientProperties.java` at v3.5.16 |
| JDK factory: Boot applies read timeout per request only if set | `SB/http/client/JdkClientHttpRequestFactoryBuilder.java` (v3.5.16) |
| Apache HttpClient 5 defaults: connect 3 min, socket 3 min (bounded) | httpclient5 `ConnectionConfig.java` DEFAULT_CONNECT_TIMEOUT; httpcore5 5.3.4 `SocketConfig.java` DEFAULT_SOCKET_TIMEOUT |

| # | Case | Expected |
|---|---|---|
| C1 | `new RestTemplate()`, no `setRequestFactory` | VIOLATION high, any Boot 3.2-3.5 (sample A1) |
| C2 | static `RestClient.builder()`, no factory, direct deps without HttpClient5/Jetty/Reactor | CONDITIONAL (transitive classpath unresolved) (sample A2) |
| C3 | injected `RestClient.Builder`, `spring.http.client.read-timeout` set, Boot 3.4/3.5 | SATISFIED (sample A3) |
| C4 | same YAML on Boot 3.3 | CONDITIONAL; property ignored by 3.3, reported as such |
| C5 | Apache HttpClient 5 on classpath, no timeouts | not applicable (bounded at 3 min). A deadline-mismatch rule is a later candidate |
| C6 | `WebClient` on Reactor Netty without response timeout, 3.4 | CONDITIONAL |
| C7 | factory constructed explicitly with timeouts | SATISFIED |

Overlap: Semgrep subset had no Spring client timeout rule. Python: Ruff S113 and Bandit B113 flag `requests` without timeout and `httpx` with `timeout=None`; neither flags `httpx` calls without a timeout argument (correct, because of the 5s default). Delegated.

---

## FN-CFG-001 Risky setting in the effective production profile

Risky settings in v0.1: actuator web exposure that includes `*`, `heapdump`, `env`, `threaddump`, `shutdown` or `configprops`; `management.endpoint.shutdown` enabled (3.2/3.3) or access not NONE (3.4+); `server.error.include-stacktrace: always`; FastAPI `debug=True` in the app object used by the effective container command; `--reload` in the effective uvicorn or gunicorn command.

### Profile resolution test matrix

| # | Signals | Expected |
|---|---|---|
| F1 | Deployment env `SPRING_PROFILES_ACTIVE=prod`, `application-prod.yml` exposes `*` | VIOLATION medium (declared linkage) |
| F2 | F1 with inferred linkage | CONDITIONAL (sample A default mode) |
| F3 | risky key only in `application-dev.yml`, prod active | no finding (sample A trap; Semgrep flags it) |
| F4 | no profile signal anywhere, no external env source | evaluate base `application.yml` only; profile files reported as not active |
| F5 | no profile signal, external `envFrom` attached | base file evaluated; failing checks CONDITIONAL with the assumption "no profile is activated outside the repository"; profile-file findings not produced; coverage notice |
| F6 | multi-document `application.yml` with `spring.config.activate.on-profile: prod` | treated as prod document |
| F7 | `prod,eu` active, both set the key | `eu` wins (later profile overrides) |
| F8 | `--profile staging` on CLI, Deployment says prod | CLI used, coverage notice records the conflict |
| F9 | literal `spring.profiles.group.prod: [prod-db, prod-obs]` | expanded |
| F10 | `spring.config.import` present | keys present in the repository: CONDITIONAL (`external_override_file`); keys the rule needs that exist only in the import: UNKNOWN |
| F11 | `application.properties` and `application.yml` in the same location | `.properties` wins |
| F12 | `SpringApplication.setAdditionalProfiles(...)` in code | CONDITIONAL with the code location |
| F13 | uvicorn `--reload` in Dockerfile CMD, Deployment `command` overrides | no finding (sample B trap) |
| F14 | uvicorn `--reload` in Dockerfile CMD, Deployment does not override | CONDITIONAL until linkage declared |

Overlap: Semgrep `spring-actuator-fully-enabled-yaml` flags `exposure.include: "*"` in any YAML file, including inactive profiles.

---

## FN-NET-001 Management surface reachable from outside the cluster

| # | Case | Expected |
|---|---|---|
| N1 | DIFFERENT management port exposed by a `LoadBalancer` or `NodePort` Service | VIOLATION high |
| N2 | DIFFERENT port only behind ClusterIP; Ingress routes app port | SATISFIED (sample A trap) |
| N3 | SAME port, Ingress path `/` (Prefix) to the app port, exposure includes more than health | VIOLATION; severity from FN-CFG-001 exposure |
| N4 | N3 with default exposure (health only) | VIOLATION low; health details exposure noted |
| N5 | Ingress path `/orders` only, SAME port | SATISFIED |
| N6 | Ingress controller-specific rewrite or auth annotations | UNKNOWN for that path (controller semantics out of scope) |

Overlap: kube-linter `exposed-services` (off by default) flags any NodePort or LoadBalancer Service without knowing which port is administrative.

---

## External override handling (applies to every rule)

| # | Mechanism | Fact effect |
|---|---|---|
| X1 | Literal env in the Deployment | resolved; beats `application.yml` via relaxed binding |
| X2 | `envFrom` of a ConfigMap in the repo | resolved |
| X3 | `envFrom` of a Secret or ConfigMap not in the repo | `env_possible` on env-bindable keys of that container |
| X4 | `valueFrom` an out-of-repo source for a named variable | that key `unresolved` |
| X5 | out-of-repo ConfigMap volume at `<WORKDIR>`, `<WORKDIR>/config` or `<WORKDIR>/config/*` | `file_possible` on all Spring keys of that container |
| X6 | `SPRING_CONFIG_LOCATION` / `SPRING_CONFIG_ADDITIONAL_LOCATION` to a mounted path | resolved if the source is in the repo, else `file_possible` |
| X7 | `SPRING_APPLICATION_JSON` literal | parsed; precedence above OS environment variables (Spring reference, Externalized Configuration) |
| X8 | `spring-cloud-starter-kubernetes-*-config` on the classpath | `file_possible` (config read from the API server at runtime) |
| X9 | Helm or Kustomize sources not rendered | k8s side UNKNOWN, coverage notice with the render command (E3) |
| X10 | gunicorn CLI flag | `none`: CLI applied last |
| X11 | pydantic-settings field default | `env_possible` when the workload context is unknown or has an out-of-repo env source |

Policy (M0-D2 as revised by the CEO, implemented in `derive_verdict()` and the `Finding` validators):

| Situation for a material input | Verdict when the check fails | Assumption recorded |
|---|---|---|
| Value in the repository, no override mechanism | VIOLATION | none |
| Value in the repository, `env_possible` | CONDITIONAL, computation kept, `verdict_if_assumptions_hold: VIOLATION` | `external_override_env` (open) |
| Value in the repository, `file_possible` | CONDITIONAL, same | `external_override_file` (open) |
| Team declared the assumption in `.fifthnine.yml` | VIOLATION, citing the declaration | same kind, `declared_by_team` with location |
| No value in the repository (placeholder without default, out-of-repo `valueFrom`, version unknown so default unknown) | UNKNOWN with the reason | none |

The same mechanism covers non-config context: inferred or stale linkage, unresolved classpath, admission defaults, GitOps behaviour, out-of-repo routing and untested versions.
