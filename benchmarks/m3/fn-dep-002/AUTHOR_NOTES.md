# Author notes

## Method

I planned the 36 cases as a grid before writing any files. The grid had four Boot versions with 9 cases each, a target class mix, and the dimensions in the brief. I then derived every expectation by tracing the request path by hand:

1. Resolve each property's effective value with Spring Boot's precedence rules: command line args, then OS environment, then external config files, then packaged profile-specific files, then packaged application files. Kubernetes `env` beats `envFrom` for the same key, and `$(VAR)` in args is expanded by the kubelet.
2. Compute `ManagementPortType` (SAME / DIFFERENT / DISABLED) exactly as `ManagementPortType.get` does, including its two special cases. When server.port is null and the management port is 8080, the type is SAME. A negative management port gives DISABLED.
3. From that, work out which server serves actuator, what its servlet context path is, and what the endpoint base path and health mapping are.
4. Decide whether the health endpoint exists and is web-exposed. Before 3.4 that means `enabled` / `enabled-by-default`. From 3.4 it means access (`access`, `access.default`, `max-permitted`), plus the legacy keys, which are still honoured. Then apply the include/exclude exposure filter.
5. Decide whether the `liveness` / `readiness` groups exist (`ProbesCondition`), and whether `/livez` and `/readyz` are mapped and on which server.
6. Resolve each probe's port as the kubelet does (int as-is, name via `container.ports`), then compare it with the server and path from steps 3 to 5.

I did not run Maven, Docker or a cluster. Every expectation is a paper derivation. The harness run is the first empirical check.

## Sources relied on

Spring Boot source, read at the exact tags. I used the local trees in `sb/v3.x` where the file was present (ManagementPortType, AvailabilityProbes*, CloudPlatform, WebEndpointProperties, ManagementServerProperties, HealthEndpointProperties, WebEndpointAutoConfiguration). I fetched the rest from raw.githubusercontent.com at the same tags:
- `ManagementContextAutoConfiguration`, `ServletManagementChildContextConfiguration`, `ManagementWebServerFactoryCustomizer`: child context port, address and context path.
- `WebMvcEndpointManagementContextConfiguration`, `HealthEndpointWebExtensionConfiguration`, `AdditionalHealthEndpointPathsWebMvcHandlerMapping`: where `/livez` and `/readyz` are mapped, and the WEB exposure condition on them.
- `OnAvailableEndpointCondition` (3.2/3.3 enablement vs 3.4/3.5 access), `PropertiesEndpointAccessResolver` (present at v3.4.13, HTTP 404 at v3.3.13), `IncludeExcludeEndpointFilter`, `EndpointExposure`.
- `AvailabilityHealthContributorAutoConfiguration`, `HealthEndpointConfiguration` (group membership validation).

Spring Boot reference docs (asciidoc sources at v3.5.16, plus v3.2.12 endpoints.adoc): actuator/endpoints.adoc (exposure, access, Kubernetes probes, additional paths), actuator/monitoring.adoc (base-path relative to context path vs management.server.base-path, port -1), features/external-config.adoc (precedence order, config file locations, on-cloud-platform, env var naming), how-to/deployment/cloud.adoc (Kubernetes detection, spring.main.cloud-platform).

Kubernetes source at v1.31.0: `pkg/probe/util.go` (named port resolution), `pkg/probe/http/http.go` and `request.go` (success is 200 to 399, port resolution), `pkg/kubelet/kubelet_pods.go` (master service env vars even with `enableServiceLinks: false`, envFrom/configMapKeyRef optional handling), `staging/src/k8s.io/api/core/v1/types.go` (command/args vs ENTRYPOINT/CMD, `$(VAR)` expansion, env over envFrom). I also used the kubernetes.io probe and command/args task pages and the Dockerfile reference for shell-form ENTRYPOINT.

I could not fetch docs.spring.io directly (it is not on this session's allowlist), so all doc citations point at the asciidoc sources on GitHub with line anchors.

## Conventions in case.yaml

- `expected_http` is what the endpoint returns to the probe request: `200`, `404`, `connection_refused`. The kubelet counts 200 to 399 as success. No case depends on a 3xx.
- When a startup probe fails, the kubelet never runs readiness or liveness. For those probes I still record what they would get, with a note.
- NOT_DETERMINABLE cases have two runtime alternatives each. The harness should apply exactly one alternative, and apply it before the Deployment. Env vars from envFrom and configMapKeyRef are read only when the container starts. In pb-36 the ConfigMap is optional, so if it is applied after the pod has started, the pod has to be restarted.
- pb-29 and pb-35 ship ConfigMaps in `k8s/`. If the Deployment is applied first, the kubelet retries until they exist, so ordering only costs time.

## Cases I am least sure about

- **pb-36 (classification, not mechanics).** The envFrom ConfigMap is `optional: true` and absent from the repo. With the repo alone the pod starts and the probes pass. I classed it NOT_DETERMINABLE because a ConfigMap named `*-overrides` is evidence that production supplies one, and its keys outrank application.yml. A reasonable grader could argue for ALIGNED. Of the 36, this is the case where my call is most open to dispute.
- **pb-14.** I expect 404 for `/actuator/health/readiness` because no group named `readiness` exists. The health endpoint also falls back to looking up a contributor with that path segment. The registered indicators are named `readinessState` and `livenessState`, not `readiness` and `liveness`, so that lookup also misses. I am confident, but this depends on the indicator bean names, which I read from the auto-configuration and did not run.
- **pb-02, pb-11 (exact status, not class).** Requests outside the only Tomcat context (`/svc`, `/returns`) should get a 404 from Tomcat. Any non-2xx/3xx result gives the same class, so only the exact code is uncertain.
- **pb-17, pb-19.** With a separate management port, `/livez` and `/readyz` sit on the main port because the handler mapping is a bean in the parent context, registered with the main DispatcherServlet. The docs say so explicitly and the code agrees. I list these cases only because they are the ones that depend most on how parent and child contexts split the beans.
- **pb-08.** `base-path: /` is cleaned to the empty string, and the root-path guard passes because health is mapped to `healthz`. I read this from source, and both the guard and the cleaning are short and unambiguous.

Every other case rests on code paths that I read line by line at the target tag.

## Deliberately left out

- `management.server.address` (for example 127.0.0.1 on a separate port): left out to keep the network setup simple. Setting the address while on the SAME port makes startup fail.
- `scheme: HTTPS` probes, management SSL, and Spring Security on actuator.
- `spring.mvc.servlet.path` (DispatcherServlet prefix). I believe actuator moves under it on the same port, but I did not verify the request-path handling to the standard I used elsewhere.
- Health group membership validation failures, such as a group including `db` with no DataSource, which aborts startup. I verified the validator (default on since 3.1) but chose not to spend a case on a startup crash.
- `management:`-prefixed `additional-path` and the `management.server.port=-1` plus `add-additional-paths` combination. In the second, the SERVER-namespace handler mapping might still register on the main port. I did not trace it far enough to commit to an answer.
- Service-link env collisions (a Service named `server` injecting `SERVER_PORT=tcp://...`). This is realistic, but the result depends on whether the Service exists before the pod starts, which the harness may not control.
- `JAVA_TOOL_OPTIONS`, `SPRING_APPLICATION_JSON`, `spring.config.import` / configtree, WebFlux, Jersey, Helm and Kustomize.
