# FifthNine

FifthNine is a reliability standard of 16 checks for services that aim for 99.999% availability, plus a Claude Code skill that scores a repository against it and prints a one-page scorecard.

Install from a clone of this repository:

```
mkdir -p ~/.claude/skills && cp -r skill/fifthnine ~/.claude/skills/
```

Then open your service's repository in Claude Code and type `run fifthnine`.

## Reference scorecard

[`examples/reference-service/`](examples/reference-service/) is a minimal service written to pass every check. This is run 1 of two on standard 1.0; both runs gave the same 16 results ([full record](examples/reference-service/SCORECARD.md)).

```
FifthNine scorecard          standard 1.0
Repo: ramkumar1308/fifthnine-dev (reference-service)   Commit: 17b5d30
Platform: Kubernetes         Languages: Python

Score: 16 of 16 checks passed (100)
Verdict: Ready

1 Embracing risk             2 of 2
2 Service level objectives   2 of 2
3 Eliminating toil           2 of 2
4 Monitoring                 2 of 2
5 Automation                 2 of 2
6 Release engineering        2 of 2
7 Simplicity                 2 of 2
8 Security                   2 of 2

Results
FN-1.1  Pass  examples/reference-service/deploy/base/deployment.yaml:8 (replicas 3), :35 (zone spread), examples/reference-service/deploy/base/pdb.yaml:8 (minAvailable 2)
FN-1.2  Pass  examples/reference-service/service.py:117 (timeout on every attempt), :110 and :54 (attempts capped at 1-5), :131 (backoff with jitter)
FN-2.1  Pass  examples/reference-service/slo/slos.yaml:30 (99.9% availability), :61 (99% under 300 ms), :10 (30d window)
FN-2.2  Pass  examples/reference-service/deploy/base/prometheusrule.yaml:34 (multi-window burn rate), :41, :48, :55
FN-3.1  Pass  examples/reference-service/deploy/base/hpa.yaml:12 (min 3), :13 (max 12), :17 (cpu metric)
FN-3.2  Pass  examples/reference-service/deploy/base/deployment.yaml:76 (/healthz), :83 (/readyz), served at examples/reference-service/service.py:159, :162 on port 8080 (:60)
FN-4.1  Pass  examples/reference-service/service.py:28 (duration), :34 (count), :35 (errors), :36-37 (in-flight vs limit), :166 (/metrics), examples/reference-service/deploy/base/servicemonitor.yaml:13
FN-4.2  Pass  examples/reference-service/service.py:70 (JSON formatter with request_id), examples/reference-service/deploy/base/prometheusrule.yaml:33
FN-5.1  Pass  examples/reference-service/deploy/overlays/production/kustomization.yaml:4, examples/reference-service/deploy/argocd-application.yaml:13
FN-5.2  Pass  .github/workflows/reference-service.yml:4 (PR), :8 (push), :36 (unit tests), :38 (image build)
FN-6.1  Pass  examples/reference-service/deploy/base/deployment.yaml:48 (1.0.0), examples/reference-service/Dockerfile:1 (python:3.12.8-slim)
FN-6.2  Pass  examples/reference-service/deploy/base/deployment.yaml:14 (maxUnavailable 0), :9 (revisionHistoryLimit 10), examples/reference-service/RUNBOOK.md:9
FN-7.1  Pass  examples/reference-service/deploy/base/deployment.yaml:51 and :53-65, each read at examples/reference-service/service.py:53-66
FN-7.2  Pass  examples/reference-service/deploy/base/deployment.yaml:56 (INFO), examples/reference-service/requirements.txt:1 imported at examples/reference-service/service.py:24
FN-8.1  Pass  examples/reference-service/deploy/base/deployment.yaml:67 (secretKeyRef), examples/reference-service/deploy/base/externalsecret.yaml:11
FN-8.2  Pass  examples/reference-service/deploy/base/deployment.yaml:28 (runAsNonRoot), :97 (no escalation), :98 (not privileged), :102 (drop ALL), examples/reference-service/Dockerfile:12 (USER 10001)
```

## Demo scorecard

Run 1 of two on checkoutservice in Google's microservices-demo, standard 1.0. Both runs gave the same 16 results ([full record](examples/microservices-demo.md)).

```
FifthNine scorecard          standard 1.0
Repo: GoogleCloudPlatform/microservices-demo (checkoutservice)   Commit: c7cb6d5
Platform: Kubernetes         Languages: Go

Score: 5 of 16 checks passed (31)
Verdict: Not ready (score below 70; critical checks FN-1.1 and FN-1.2 failed)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           1 of 2
4 Monitoring                 0 of 2
5 Automation                 1 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   2 of 2

Results
FN-1.1  Fail         kubernetes-manifests/checkoutservice.yaml:21
FN-1.2  Fail         src/checkoutservice/main.go:214
FN-2.1  No evidence  none
FN-2.2  No evidence  none
FN-3.1  Fail         kubernetes-manifests/checkoutservice.yaml:21
FN-3.2  Pass         kubernetes-manifests/checkoutservice.yaml:48
FN-4.1  Fail         src/checkoutservice/main.go:150
FN-4.2  No evidence  none
FN-5.1  Pass         terraform/main.tf:40
FN-5.2  Fail         .github/workflows/ci-pr.yaml:54
FN-6.1  Fail         src/checkoutservice/Dockerfile:33
FN-6.2  Fail         kubernetes-manifests/checkoutservice.yaml:21
FN-7.1  Pass         kubernetes-manifests/checkoutservice.yaml:47
FN-7.2  Fail         src/checkoutservice/main.go:54
FN-8.1  Pass         kubernetes-manifests/checkoutservice.yaml:54
FN-8.2  Pass         kubernetes-manifests/checkoutservice.yaml:34

Fix first
1. FN-1.1  No replicas set (defaults to 1), no topology spread and no PodDisruptionBudget
           kubernetes-manifests/checkoutservice.yaml:21
2. FN-1.2  gRPC clients have no call deadline or retry cap; the 3s timeout context is thrown away
           src/checkoutservice/main.go:214
3. FN-3.1  No HorizontalPodAutoscaler for checkoutservice, and no replica settings on the Deployment
           kubernetes-manifests/checkoutservice.yaml:21
4. FN-4.1  No metrics are exported; initStats is an empty TODO
           src/checkoutservice/main.go:150
5. FN-5.2  CI does not run checkoutservice's own unit tests (money_test.go is never run)
           .github/workflows/ci-pr.yaml:54

No evidence: FN-2.1 (no SLO file or written availability and latency target), FN-2.2 (no alert rule files in the repo), FN-4.2 (logs are JSON at src/checkoutservice/main.go:55, but there are no alert rule files)
```

The three public repos in [`examples/`](examples/) (microservices-demo, spring-petclinic-microservices and podinfo) are demo apps, not production services, so low scores are expected.

Scores can differ by one check between runs; trust the verdict and the critical checks.

## The standard

Eight factors, 16 checks. Critical checks block a Ready verdict when they fail. The full text, with the evidence each check needs, is in [FIFTHNINE.md](skill/fifthnine/FIFTHNINE.md).

| Factor | Checks |
| --- | --- |
| [1 Embracing risk](skill/fifthnine/FIFTHNINE.md#factor-1-embracing-risk) | [FN-1.1](skill/fifthnine/FIFTHNINE.md#fn-11-redundant-instances-across-zones-critical) Redundant instances across zones (critical), [FN-1.2](skill/fifthnine/FIFTHNINE.md#fn-12-timeouts-and-retry-limits-critical) Timeouts and retry limits (critical) |
| [2 Service level objectives](skill/fifthnine/FIFTHNINE.md#factor-2-service-level-objectives) | [FN-2.1](skill/fifthnine/FIFTHNINE.md#fn-21-a-written-target) A written target, [FN-2.2](skill/fifthnine/FIFTHNINE.md#fn-22-error-budget-alerts) Error budget alerts |
| [3 Eliminating toil](skill/fifthnine/FIFTHNINE.md#factor-3-eliminating-toil) | [FN-3.1](skill/fifthnine/FIFTHNINE.md#fn-31-autoscaling) Autoscaling, [FN-3.2](skill/fifthnine/FIFTHNINE.md#fn-32-probes-that-point-to-real-endpoints-critical) Probes that point to real endpoints (critical) |
| [4 Monitoring](skill/fifthnine/FIFTHNINE.md#factor-4-monitoring) | [FN-4.1](skill/fifthnine/FIFTHNINE.md#fn-41-the-four-golden-signals) The four golden signals, [FN-4.2](skill/fifthnine/FIFTHNINE.md#fn-42-structured-logs-and-alert-rules-in-the-repo) Structured logs and alert rules in the repo |
| [5 Automation](skill/fifthnine/FIFTHNINE.md#factor-5-automation) | [FN-5.1](skill/fifthnine/FIFTHNINE.md#fn-51-infrastructure-as-code) Infrastructure as code, [FN-5.2](skill/fifthnine/FIFTHNINE.md#fn-52-build-and-test-on-every-change) Build and test on every change |
| [6 Release engineering](skill/fifthnine/FIFTHNINE.md#factor-6-release-engineering) | [FN-6.1](skill/fifthnine/FIFTHNINE.md#fn-61-pinned-artifacts) Pinned artifacts, [FN-6.2](skill/fifthnine/FIFTHNINE.md#fn-62-gradual-rollout-with-a-rollback-path) Gradual rollout with a rollback path |
| [7 Simplicity](skill/fifthnine/FIFTHNINE.md#factor-7-simplicity) | [FN-7.1](skill/fifthnine/FIFTHNINE.md#fn-71-no-unused-ports-variables-or-config) No unused ports, variables or config, [FN-7.2](skill/fifthnine/FIFTHNINE.md#fn-72-no-debug-flags-or-unused-dependencies-in-production) No debug flags or unused dependencies in production |
| [8 Security](skill/fifthnine/FIFTHNINE.md#factor-8-security) | [FN-8.1](skill/fifthnine/FIFTHNINE.md#fn-81-no-secrets-in-code-or-config-critical) No secrets in code or config (critical), [FN-8.2](skill/fifthnine/FIFTHNINE.md#fn-82-non-root-and-least-privilege) Non-root and least privilege |

## Add a custom check

Append an entry to `FIFTHNINE.md` in your installed copy (`~/.claude/skills/fifthnine/`), with your own prefix:

```
### ACME-1 Runbook link
Every service links its runbook in the README.
- Evidence: a runbook URL in README.md.
```

Add Why, Prevents and Fix lines too if you want them in explanations. Custom checks are reported in their own section of the scorecard and do not change the standard score. See [Custom checks](skill/fifthnine/FIFTHNINE.md#custom-checks).

## Deterministic engine (in development)

`src/` holds an offline scanner that needs no AI model, still in development. Its current build (M1) installs, walks a repository, detects frameworks and versions, and reports `INCOMPLETE`. It has no reliability rules yet. See [`docs/fifthnine/M1.md`](docs/fifthnine/M1.md) and [`docs/decisions/2026-11-21-v1-skill.md`](docs/decisions/2026-11-21-v1-skill.md).

```
python -m pip install -e ".[dev]"
fifthnine scan .
python -m pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
