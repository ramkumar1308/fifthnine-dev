# The FifthNine standard, version 1.0

FifthNine is a production-readiness standard for services that aim for 99.999% availability. It has eight factors and 16 checks. Each check can be verified from files in a repository.

## How to read a check

Every check has an ID, one sentence that states it, and four fields. "Why" gives the reason. "Prevents" names the outage it guards against. "Evidence" says what to look for in the repo. "Fix" gives the usual remedy. Checks that look for a setting that may be missing have a fifth field, "Where it belongs", which names the files that should hold the setting on each platform (see "Missing settings" below). A check marked critical blocks a "Ready" verdict when it fails.

Each check states an intent that holds for any language and any platform. The evidence lines give container and Kubernetes examples because those are the most common. On another platform the same intent is checked against that platform's own files. A check is marked not applicable only when its intent cannot apply, such as the outbound-call check for a service that calls nothing.

## Any language, any platform

FifthNine judges a service from the SRE point of view. The language can be Rust, Python, Java, .NET, C, C++ or COBOL. The table shows how five intents map to four platforms.

| Intent | Kubernetes | Virtual machines | Serverless | Mainframe |
| --- | --- | --- | --- | --- |
| Redundancy (FN-1.1) | Replicas across zones, PodDisruptionBudget | Instance group across zones behind a load balancer | Provided by the platform | Parallel Sysplex, more than one CICS region |
| Health checks (FN-3.2) | Liveness and readiness probes | Load balancer health checks, service restart policy | Provided by the platform | Automatic Restart Manager policy |
| Scaling (FN-3.1) | HorizontalPodAutoscaler | Autoscaling group with a minimum and maximum | Concurrency limits | Workload Manager goals |
| Pinned artifacts (FN-6.1) | Image tag or digest | Versioned package or machine image | Pinned function version or alias | Versioned load modules under change control |
| Least privilege (FN-8.2) | Non-root securityContext | Dedicated non-root service account | Role scoped to the function | RACF profile scoped to the job |

The published example scorecards cover container and Kubernetes repositories. On other platforms the agent maps each intent itself, so review those results more closely.

## Factor 1: embracing risk

Failure is expected. The service must survive the loss of one instance, one zone or one dependency.

### FN-1.1 Redundant instances across zones (critical)

At least two instances run in separate failure zones, and planned maintenance cannot take them all down at once.

- Why: a single instance turns every node failure and every node upgrade into an outage.
- Prevents: full downtime during node drains, zone failures and cluster upgrades.
- Evidence: a replica count of two or more, or an autoscaler minimum of two or more. Topology spread constraints or pod anti-affinity across zones. A PodDisruptionBudget that selects the workload.
- Where it belongs: Kubernetes: the Deployment or StatefulSet manifest (replicas, topology spread, affinity), a HorizontalPodAutoscaler manifest (minimum), and a PodDisruptionBudget manifest. Docker Compose: the service's entry in the compose file. Virtual machines: the instance group or autoscaling group definition.
- Fix: set replicas to at least two, add a topology spread constraint on the zone label, and add a PodDisruptionBudget with minAvailable of one or more.

### FN-1.2 Timeouts and retry limits (critical)

Every outbound call has a timeout and a retry limit.

- Why: a call with no timeout can wait forever, and unlimited retries multiply load on a dependency that is already struggling.
- Prevents: thread and connection exhaustion caused by one slow dependency. Retry storms that turn a partial failure into a full one.
- Evidence: explicit timeouts on HTTP, gRPC, database and queue clients. Retries with a maximum attempt count and backoff. A client built on library defaults fails the check when the default is no timeout.
- Where it belongs: any platform: the code that creates each client, or the config file that client reads its timeouts and retries from, such as a Spring `application.yml` or a .NET `appsettings.json`.
- Fix: set connect and read timeouts on every client. Cap retries, three attempts being a common ceiling, with exponential backoff and jitter.

## Factor 2: service level objectives

A service needs a stated target, and alerts that measure against it.

### FN-2.1 A written target

An availability and latency target is written in the repo.

- Why: without a target nobody can say whether the service is reliable enough, and release decisions become opinion.
- Prevents: slow degradation that nobody is accountable for.
- Evidence: an SLO definition file, for example in OpenSLO or Sloth format. SLO recording rules. A document in the repo that states an availability target and a latency target with a measurement window.
- Fix: add an SLO file that states the indicator, the target and the window, for example 99.9% of requests succeed over 30 days.

### FN-2.2 Error budget alerts

Alerts fire on error budget burn against that target.

- Why: alerts tied to the budget page people when users are harmed and stay quiet otherwise.
- Prevents: outages that customers report before the team sees them. Alert fatigue from alerts on causes that do not affect users.
- Evidence: alert rules that compute burn rate against the SLO, usually with a fast window and a slow window.
- Where it belongs: any platform: an alert rule file, such as a PrometheusRule manifest or a Prometheus rule file, or the `rule_files` list of a Prometheus config.
- Fix: add multi-window burn-rate alerts for each SLO.

## Factor 3: eliminating toil

The platform should scale and recover the service with no person involved.

### FN-3.1 Autoscaling

Autoscaling is configured with a minimum and a maximum.

- Why: manual scaling is repetitive work and arrives too late for a traffic spike.
- Prevents: overload during peaks.
- Evidence: a HorizontalPodAutoscaler or KEDA ScaledObject that targets the workload, with minReplicas, maxReplicas and a scaling metric set.
- Where it belongs: Kubernetes: a HorizontalPodAutoscaler or KEDA ScaledObject manifest, or the Deployment's own replica settings. Docker Compose: the service's entry in the compose file. Virtual machines: the autoscaling group or instance group definition. Serverless: the function's concurrency settings.
- Fix: add an autoscaler with a minimum of two and a maximum sized from load tests. Set resource requests so CPU-based scaling can work.

### FN-3.2 Probes that point to real endpoints (critical)

Health checks exist for liveness and readiness, and they point to real endpoints.

- Why: probes let the platform restart hung instances and hold traffic back from instances that are not ready.
- Prevents: restart loops caused by a probe path the application does not serve. Traffic sent to instances that are still starting.
- Evidence: liveness and readiness probes on every container that serves traffic. The probe path and port match an endpoint and port the application exposes in its code or config.
- Where it belongs: Kubernetes: the container spec in the Deployment or StatefulSet manifest. Docker Compose: the service's `healthcheck` and `restart` settings in the compose file. Virtual machines: the load balancer health check definition and the service unit file.
- Fix: add both probes and point them at the application's health endpoints. Add a startup probe for slow-starting applications.

## Factor 4: monitoring

The team must be able to see user impact and how close the service is to its limits.

### FN-4.1 The four golden signals

Metrics cover latency, traffic, errors and saturation.

- Why: these four show whether users are being served and how much headroom is left.
- Prevents: blind spots during an incident. Saturation that goes unseen until requests fail.
- Evidence: a metrics library and endpoint in the application, for example a Prometheus client, Micrometer or OpenTelemetry. Metrics for request duration, request count and error count, plus a saturation measure such as pool usage or queue depth. A scrape config or ServiceMonitor. The metrics endpoint must be exposed by config in this repo. A metrics library alone is No evidence. Saturation means a resource measure such as pool, queue, CPU or memory, not request counts.
- Where it belongs: any platform: the metrics code in the application, the config that exposes the metrics endpoint (such as Spring's `management` settings in `application.yml`), and the scrape config or ServiceMonitor.
- Fix: instrument request handlers with duration, count and error metrics. Export pool and resource usage. Expose a metrics endpoint.

### FN-4.2 Structured logs and alert rules in the repo

Logs are structured and alert rules live in the repo.

- Why: structured logs can be searched during an incident. Alert rules kept in the repo are reviewed and versioned with the code.
- Prevents: slow diagnosis. Alerts that drift from the code or disappear with a dashboard.
- Evidence: JSON or key-value log configuration. Alert rule files in the repo, for example PrometheusRule manifests.
- Where it belongs: any platform: for the log format, the logging config file (such as `logback.xml`, `log4j2.xml` or `logging.yaml`) or the code that sets up the logger. For alert rules, the files listed under FN-2.2.
- Fix: configure JSON logging with a request or trace ID on every line. Move alert rules into the repo.

## Factor 5: automation

Machines build environments and verify changes. People review.

### FN-5.1 Infrastructure as code

Infrastructure is defined as code, with no manual setup steps.

- Why: an environment built by hand cannot be rebuilt quickly or identically after a failure.
- Prevents: long recovery after losing an environment. Config drift between staging and production.
- Evidence: Terraform, Pulumi, CloudFormation, Helm charts or Kustomize overlays that cover what the service needs. No README steps that tell a person to click through a console or run commands by hand to set up production. Only required steps count. A step the docs mark as optional, or as an alternative to the coded path, is not a manual setup step. Cloud account, billing and permission prerequisites are not manual setup steps for FN-5.1.
- Fix: move each manual setup step into code and delete it from the docs.

### FN-5.2 Build and test on every change

The pipeline builds and tests on every change.

- Why: a change that no machine built or tested reaches production on trust alone.
- Prevents: broken builds and regressions shipped to production.
- Evidence: a pipeline file, such as a GitHub Actions workflow, a GitLab CI file or a Jenkinsfile, that is triggered on pull requests or pushes and has build and test steps. The pipeline runs this service's own tests. An end-to-end test alone does not count.
- Fix: add a pipeline that builds and runs the tests on every pull request and blocks the merge on failure.

## Factor 6: release engineering

A release should be repeatable, gradual and reversible.

### FN-6.1 Pinned artifacts

Every deployed artifact is pinned to an exact version, never a moving tag such as "latest".

- Why: with a moving tag the same manifest can deploy different code on different days, and a rollback may not return the old version.
- Prevents: untested code entering production when a pod restarts. Rollbacks that fail.
- Evidence: every image deployed to production, and every base image in a Dockerfile, uses a specific version tag or a digest. Test hooks and example folders are excluded. No "latest" and no missing tag.
- Fix: pin each image to a version or digest and update it through the pipeline.

### FN-6.2 Gradual rollout with a rollback path

Rollout is gradual and a rollback path is defined.

- Why: releasing to every instance at once exposes every user to a bad change.
- Prevents: a full outage from one bad release. Long recovery because nobody knows how to go back.
- Evidence: a RollingUpdate strategy with maxUnavailable below the replica count, or a canary or blue-green tool such as Argo Rollouts or Flagger. A rollback that is documented or automated: revision history kept, and a rollback step in the pipeline or a runbook. When the repo has more than one production deployment path, judge the weakest one. A link to a tool's docs is not a rollback path.
- Where it belongs: Kubernetes: the strategy and revisionHistoryLimit of the Deployment manifest, or the Rollout manifest of a canary tool. For the rollback step, the pipeline file or a runbook. Docker Compose: the service's `deploy.update_config` and `deploy.rollback_config` in the compose file. Virtual machines: the instance group's update policy. Serverless: the alias or traffic-shifting config.
- Fix: set a rolling update with maxUnavailable of zero or one, gated on readiness. Document the rollback command or automate rollback on failed health checks.

## Factor 7: simplicity

Everything in production should be there for a reason.

### FN-7.1 No unused ports, variables or config

No unused ports, environment variables or dead config.

- Why: every exposed port and unused setting is one more thing to secure, understand and misconfigure.
- Prevents: outages caused by stale config taking effect. Exposure through ports nobody remembers opening.
- Evidence: each containerPort and Service port maps to a port the application listens on. Each environment variable and config key in the manifests is read by the code. This includes keys in production values files that no template renders under those values.
- Fix: remove the ports, variables and config files that nothing uses.

### FN-7.2 No debug flags or unused dependencies in production

No debug flags or unused dependencies in production.

- Why: debug modes change behavior, expose internals and slow the service. Unused dependencies add ways to fail.
- Prevents: slowdowns and data exposure from debug settings such as debug mode, trace logging, development profiles and auto-reload.
- Evidence: production config and the container command contain no debug or development settings. Each entry in the dependency manifest is imported by the code.
- Fix: turn off debug and reload settings in production config. Delete dependencies the code does not import.

## Factor 8: security

A security failure is an availability failure. This factor is FifthNine's addition to the seven SRE principles.

### FN-8.1 No secrets in code or config (critical)

No secrets or keys in code or config.

- Why: a secret in the repo is readable by everyone with access and stays in the history after deletion.
- Prevents: credential theft that leads to data loss or a takedown. Emergency rotations.
- Evidence: no passwords, tokens, private keys or connection strings with credentials in code, config, manifests or Dockerfiles. Secrets are referenced from a secret store or through Kubernetes Secret references.
- Fix: rotate the exposed secret first. Then move it to a secret manager and reference it at runtime.

### FN-8.2 Non-root and least privilege

The service runs under a non-root identity with least privilege.

- Why: a compromised process running as root in a privileged container can reach the node and other workloads.
- Prevents: one compromised service taking down others.
- Evidence: runAsNonRoot set to true, or a non-zero user in the Dockerfile or securityContext. allowPrivilegeEscalation set to false. Capabilities dropped. No privileged containers.
- Where it belongs: Kubernetes: the pod and container securityContext in the workload manifest, and the Dockerfile `USER`. Docker Compose: the service's `user` setting in the compose file, and the Dockerfile `USER`. Virtual machines: the service unit file (`User=`). Serverless: the function's role definition.
- Fix: add a non-root USER to the Dockerfile and a securityContext that drops all capabilities and disallows privilege escalation.

## Results and scoring

| Result | Meaning | Effect on score |
| --- | --- | --- |
| Pass | Evidence in the repo shows the check is met | Full points |
| Fail | Evidence in the repo shows the check is not met | Zero |
| No evidence | Nothing in the repo proves it either way | Zero, listed separately |
| Not applicable | The check does not fit this repo | Removed from the total |

All applicable checks carry equal weight and the score is scaled to 100. Round half up to a whole number. Show the arithmetic as passed / applicable. A score of 90 or above with no failed critical check is "Ready". A score of 70 to 89 is "At risk". A score below 70, or any failed critical check, is "Not ready".

Every Pass and every Fail must cite a file and line. A result with no citation is recorded as No evidence.

### Missing settings

Some checks look for a setting that may be absent. Their "Where it belongs" field names the files that should hold it, by platform. On a platform the field does not list, use the file that holds that platform's equivalent setting.

- If one of those files exists in the repo and the setting is missing, the result is Fail, citing that file.
- If none of those files exists in the repo, the result is No evidence.
- If the setting belongs in a location outside this repo, such as an external config repository or a cloud console, the result is No evidence, and the scorecard line names that location. A setting belongs outside the repo when the repo's own config for the service loads it from that location, for example a Spring config server import, even if a local file could also hold it.
- With Helm, the files are the values file the production release uses and the templates it renders. With Kustomize, they are the bases and patches that the production overlay builds.

## Custom checks

A team adds its own check by appending an entry in the same format, with its own prefix.

```
### ACME-1 Runbook link

Every service links its runbook in the README.

- Why: the person on call needs the runbook within seconds.
- Prevents: slow response during an incident.
- Evidence: a runbook URL in README.md.
- Fix: add the link.
```

Custom checks are reported in their own section of the scorecard and leave the standard score unchanged, so scores stay comparable between teams.

## Versioning

The standard uses major and minor versions. Adding or rewording a check raises the minor version. Removing a check or changing the scoring raises the major version. Every scorecard states the version it used.

## Source

Factors 1 to 7 follow the principles section of [Google's Site Reliability Engineering book](https://sre.google/sre-book/table-of-contents/). The four golden signals in FN-4.1 come from its chapter on monitoring distributed systems.
