# FifthNine scorecard: GoogleCloudPlatform/microservices-demo (checkoutservice)

- Repository: https://github.com/GoogleCloudPlatform/microservices-demo
- Commit: c7cb6d5
- Service scored: checkoutservice (the repository holds 12 services)
- Standard: draft 2 (current) and draft 1 (first round, kept below)
- Runs: two per standard version, each in a fresh agent context, on 22 November 2026

## Draft 2

### Run 1 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: GoogleCloudPlatform/microservices-demo (checkoutservice)   Commit: c7cb6d5
Platform: Kubernetes         Languages: Go

Score: 4 of 16 checks passed (25)
Verdict: Not ready (critical checks FN-1.1 and FN-1.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           1 of 2
4 Monitoring                 0 of 2
5 Automation                 0 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   2 of 2

Results
FN-1.1  Fail         kubernetes-manifests/checkoutservice.yaml:21
FN-1.2  Fail         src/checkoutservice/main.go:360
FN-2.1  No evidence  none
FN-2.2  No evidence  none
FN-3.1  No evidence  none
FN-3.2  Pass         kubernetes-manifests/checkoutservice.yaml:48
FN-4.1  Fail         src/checkoutservice/main.go:151
FN-4.2  No evidence  none
FN-5.1  Fail         terraform/README.md:21
FN-5.2  Fail         .github/workflows/ci-pr.yaml:54
FN-6.1  Fail         src/checkoutservice/Dockerfile:33
FN-6.2  Fail         kubernetes-manifests/checkoutservice.yaml:21
FN-7.1  Pass         kubernetes-manifests/checkoutservice.yaml:47
FN-7.2  Fail         src/checkoutservice/main.go:54
FN-8.1  Pass         kubernetes-manifests/checkoutservice.yaml:54
FN-8.2  Pass         kubernetes-manifests/checkoutservice.yaml:34

Fix first
1. FN-1.1  No replicas (defaults to 1), no zone topology spread, no PodDisruptionBudget
           kubernetes-manifests/checkoutservice.yaml:21
2. FN-1.2  Outbound gRPC calls have no deadline (currency uses context.TODO()) and no retry cap
           src/checkoutservice/main.go:360
3. FN-4.1  Metrics are a TODO stub; no request, error or saturation metrics and no metrics endpoint
           src/checkoutservice/main.go:151
4. FN-5.1  Required manual step: create the GCP project and enable billing in the console
           terraform/README.md:21
5. FN-5.2  CI unit-test loop leaves out checkoutservice, so money_test.go never runs
           .github/workflows/ci-pr.yaml:54

No evidence: FN-2.1 no SLO file or document with targets; FN-2.2 no burn-rate alert rules; FN-3.1 no HorizontalPodAutoscaler or ScaledObject file for the workload; FN-4.2 JSON logging is set up (main.go:55), but the repo has no alert rule files
```

### Run 2 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: GoogleCloudPlatform/microservices-demo (checkoutservice)   Commit: c7cb6d5
Platform: Kubernetes         Languages: Go

Score: 5 of 16 checks passed (31)
Verdict: Not ready (critical checks FN-1.1 and FN-1.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           1 of 2
4 Monitoring                 0 of 2
5 Automation                 1 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   2 of 2

Results
FN-1.1  Fail         kustomize/base/checkoutservice.yaml:21
FN-1.2  Fail         src/checkoutservice/main.go:360
FN-2.1  No evidence  none
FN-2.2  No evidence  none
FN-3.1  Fail         kustomize/base/checkoutservice.yaml:21
FN-3.2  Pass         kustomize/base/checkoutservice.yaml:48, src/checkoutservice/main.go:144
FN-4.1  Fail         src/checkoutservice/main.go:151
FN-4.2  No evidence  none
FN-5.1  Pass         terraform/main.tf:40, terraform/main.tf:79
FN-5.2  Fail         .github/workflows/ci-pr.yaml:54
FN-6.1  Fail         src/checkoutservice/Dockerfile:33
FN-6.2  Fail         kustomize/base/checkoutservice.yaml:21
FN-7.1  Pass         kubernetes-manifests/checkoutservice.yaml:47, src/checkoutservice/main.go:111
FN-7.2  Fail         src/checkoutservice/main.go:54
FN-8.1  Pass         kubernetes-manifests/checkoutservice.yaml:54
FN-8.2  Pass         kubernetes-manifests/checkoutservice.yaml:34, kubernetes-manifests/checkoutservice.yaml:39

Fix first
1. FN-1.1  No replicas set (defaults to 1), no zone spread, no PodDisruptionBudget
               kustomize/base/checkoutservice.yaml:21
2. FN-1.2  Outbound gRPC calls have no deadline (context.TODO) and no retry limit; dial timeout context is discarded
               src/checkoutservice/main.go:360
3. FN-3.1  No HorizontalPodAutoscaler targets checkoutservice
               kustomize/base/checkoutservice.yaml:21
4. FN-4.1  No metrics: stats are an unimplemented TODO and only tracing/profiler are wired
               src/checkoutservice/main.go:151
5. FN-5.2  CI unit-test loop skips checkoutservice, so money_test.go never runs
               .github/workflows/ci-pr.yaml:54

No evidence: FN-2.1 no SLO file or written availability/latency target; FN-2.2 no burn-rate alert rules; FN-4.2 logs are JSON (main.go:55) but there are no alert rule files in the repo
```

### Hand check (draft 2)

Every new citation was opened and read. All of them exist and say what the run claims.

- **Pass mark missed.** The runs agree on every critical check but disagree on two checks.
- **FN-3.1:** run 1 is No evidence, run 2 is Fail. Run 2 applied the missing-configuration rule to the Deployment manifest. Run 1 held that an autoscaler belongs in its own file, which does not exist. The rule does not say which file a missing setting belongs in.
- **FN-5.1:** run 1 is Fail, run 2 is Pass. `terraform/README.md:21` asks the reader to create a Google Cloud project and enable billing. Run 1 counted that as a required manual setup step. Run 2 counted it as an account prerequisite.
- **FN-5.2 now agrees.** Both runs fail it. This is the draft 1 disagreement, settled by the new wording.

## Draft 1

### Run 1 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: GoogleCloudPlatform/microservices-demo (checkoutservice)   Commit: c7cb6d5
Platform: Kubernetes         Languages: Go

Score: 31 / 100              Verdict: Not ready (critical checks FN-1.1 and FN-1.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           1 of 2
4 Monitoring                 0 of 2
5 Automation                 1 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   2 of 2

Fix first
1. FN-1.1  Deployment sets no replicas (defaults to 1), no zone spread, no PodDisruptionBudget
           kubernetes-manifests/checkoutservice.yaml:21
2. FN-1.2  Currency calls use context.TODO() with no deadline; no gRPC timeout or retry cap on any client
           src/checkoutservice/main.go:360
3. FN-4.1  No metrics: OpenTelemetry stats left as a TODO, no metrics endpoint or scrape config
           src/checkoutservice/main.go:151
4. FN-5.2  PR and main CI run go test only for other services; checkoutservice tests never run
           .github/workflows/ci-pr.yaml:54
5. FN-6.1  Runtime base image gcr.io/distroless/static has no tag or digest
           src/checkoutservice/Dockerfile:33

No evidence: FN-2.1 (no SLO file or written availability/latency target), FN-2.2 (no burn-rate alert rules), FN-3.1 (no HPA or KEDA ScaledObject for checkoutservice), FN-4.2 (JSON logs at main.go:55 but no alert rule files in repo), FN-6.2 (no explicit rollout strategy and no documented or automated rollback)
```

### Run 2 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: GoogleCloudPlatform/microservices-demo (checkoutservice)    Commit: c7cb6d5
Platform: Kubernetes         Languages: Go

Score: 38 / 100              Verdict: Not ready (critical checks FN-1.1 and FN-1.2 failed)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           1 of 2
4 Monitoring                 0 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   2 of 2

Fix first
1. FN-1.1  Deployment sets no replicas (defaults to 1); no PDB, no zone spread
               kubernetes-manifests/checkoutservice.yaml:21
2. FN-1.2  Outbound gRPC calls have no deadline or retry cap; currency call uses context.TODO()
               src/checkoutservice/main.go:360
3. FN-4.1  No metrics are exported; initStats is an unimplemented TODO
               src/checkoutservice/main.go:151
4. FN-6.1  Runtime base image gcr.io/distroless/static has no tag or digest
               src/checkoutservice/Dockerfile:33
5. FN-7.2  Logger hard-coded to debug level in production
               src/checkoutservice/main.go:54

No evidence: FN-2.1 (no SLO file or written availability/latency target found), FN-2.2 (no burn-rate alert rules found), FN-3.1 (no HorizontalPodAutoscaler or ScaledObject for checkoutservice), FN-4.2 (JSON logging at main.go:55, but no alert rule files in repo), FN-6.2 (no explicit rollout strategy and no documented or automated rollback)
```

### Hand check (draft 1)

Every cited file and line was opened and read.

- The runs disagree on one check: FN-5.2. Run 1 is Fail, run 2 is Pass. Both citations are true. CI builds every image on each pull request and push, and runs an end-to-end smoke test (`.github/workflows/ci-main.yaml:112`, `:134`). The Go unit-test loop leaves out checkoutservice (`.github/workflows/ci-pr.yaml:54`), even though `src/checkoutservice/money/money_test.go` exists. The standard does not say whether the service's own tests must run, so the result depends on how the check is read.
- No finding was wrong. Fix lists differ only because of FN-5.2.
