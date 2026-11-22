# FifthNine scorecard: stefanprodan/podinfo

- Repository: https://github.com/stefanprodan/podinfo
- Commit: f72ffd4
- Service scored: podinfo (single service; the frontend, backend and database workloads run the same binary)
- Standard: draft 2 (current) and draft 1 (first round, kept below)
- Runs: two per standard version, each in a fresh agent context, on 22 November 2026

## Draft 2

### Run 1 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: stefanprodan/podinfo   Commit: f72ffd4
Platform: Kubernetes         Languages: Go, CUE, Shell

Score: 6 of 16 checks passed (38)
Verdict: Not ready (critical checks FN-1.1, FN-1.2 and FN-8.1 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           2 of 2
4 Monitoring                 1 of 2
5 Automation                 2 of 2
6 Release engineering        1 of 2
7 Simplicity                 0 of 2
8 Security                   0 of 2

Results
FN-1.1  Fail         charts/podinfo/values-prod.yaml:4
FN-1.2  Fail         pkg/api/http/cache.go:140
FN-2.1  No evidence  none
FN-2.2  No evidence  none
FN-3.1  Pass         charts/podinfo/values-prod.yaml:84
FN-3.2  Pass         charts/podinfo/templates/deployment.yaml:191
FN-4.1  Pass         pkg/api/http/metrics.go:25
FN-4.2  No evidence  none
FN-5.1  Pass         charts/podinfo/Chart.yaml:1
FN-5.2  Pass         .github/workflows/test.yml:37
FN-6.1  Pass         charts/podinfo/values-prod.yaml:11
FN-6.2  Fail         charts/podinfo/templates/deployment.yaml:24
FN-7.1  Fail         charts/podinfo/values-prod.yaml:39
FN-7.2  Fail         deploy/bases/frontend/deployment.yaml:40
FN-8.1  Fail         cmd/podinfo/main.go:90
FN-8.2  Fail         charts/podinfo/values-prod.yaml:111

Fix first
1. FN-1.1  Production replicaCount is 1 (HPA min 1), with no topology spread and no PDB
           charts/podinfo/values-prod.yaml:4
2. FN-1.2  Redis client is dialed with no read or write timeout and the pool has no limit
           pkg/api/http/cache.go:140
3. FN-8.1  A default JWT signing secret is hard-coded in the source
           cmd/podinfo/main.go:90
4. FN-6.2  maxUnavailable 1 equals the production replica count of 1, and no rollback path is documented
           charts/podinfo/templates/deployment.yaml:24
5. FN-7.1  The prod values set nodePort, but the Service type is ClusterIP, so no template renders it
           charts/podinfo/values-prod.yaml:39

No evidence: FN-2.1 (no SLO file or written availability or latency target); FN-2.2 (no burn-rate alert rules); FN-4.2 (JSON logging is set at cmd/podinfo/main.go:255, but the repo has no alert rule files)
```

### Run 2 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: stefanprodan/podinfo   Commit: f72ffd4
Platform: Kubernetes         Languages: Go, Shell, CUE

Score: 6 of 16 checks passed (38)
Verdict: Not ready (critical checks FN-1.1, FN-1.2 and FN-8.1 failed)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           2 of 2
4 Monitoring                 1 of 2
5 Automation                 2 of 2
6 Release engineering        1 of 2
7 Simplicity                 0 of 2
8 Security                   0 of 2

Results
FN-1.1  Fail         charts/podinfo/values-prod.yaml:4
FN-1.2  Fail         pkg/api/http/cache.go:140
FN-2.1  No evidence  none
FN-2.2  No evidence  none
FN-3.1  Pass         charts/podinfo/values-prod.yaml:84
FN-3.2  Pass         charts/podinfo/templates/deployment.yaml:185
FN-4.1  Pass         pkg/api/http/metrics.go:25
FN-4.2  No evidence  none
FN-5.1  Pass         deploy/overlays/production/kustomization.yaml:1
FN-5.2  Pass         .github/workflows/test.yml:37
FN-6.1  Pass         charts/podinfo/values-prod.yaml:11
FN-6.2  Fail         charts/podinfo/templates/deployment.yaml:24
FN-7.1  Fail         charts/podinfo/values-prod.yaml:39
FN-7.2  Fail         deploy/bases/frontend/deployment.yaml:40
FN-8.1  Fail         cmd/podinfo/main.go:90
FN-8.2  Fail         charts/podinfo/values-prod.yaml:111

Fix first
1. FN-1.1  Production runs one replica (HPA min is also 1), with no zone spread and no PDB rendered
           charts/podinfo/values-prod.yaml:4
2. FN-1.2  Redis client is dialed with no connect, read or write timeout
           pkg/api/http/cache.go:140
3. FN-8.1  A hard-coded default JWT signing secret is in the code
           cmd/podinfo/main.go:90
4. FN-6.2  maxUnavailable 1 with 1 replica, and the only rollback guidance is a link to Flux docs
           charts/podinfo/templates/deployment.yaml:24
5. FN-7.1  nodePort is set but the Service type is ClusterIP, so no template renders it
           charts/podinfo/values-prod.yaml:39

No evidence: FN-2.1 (no SLO file or written availability and latency target), FN-2.2 (no burn-rate alert rules), FN-4.2 (JSON logging is configured at cmd/podinfo/main.go:255, but there are no alert rule files in the repo)
```

### Hand check (draft 2)

Every new citation was opened and read. All of them exist and say what the run claims. The FN-8.1 secret value was not copied.

- **Pass mark met.** Both runs agree on all 16 results.
- **The draft 1 disagreements are settled.** FN-6.2 and FN-7.1 now agree. The "weakest path" and "values files" wording took effect.
- **FN-6.1 changed from Fail to Pass.** Test hooks and example folders are now excluded, and the production image is pinned to `6.17.0` (`charts/podinfo/values-prod.yaml:11`).
- **FN-6.1 is still borderline.** The Dockerfile bases `golang:1.27-alpine` and `alpine:3.24` are minor-version tags that pick up patch releases. Run 1 raised this itself. The standard does not say whether a minor-version tag counts as "a specific version".

## Draft 1

### Run 1 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: stefanprodan/podinfo   Commit: f72ffd4
Platform: Kubernetes         Languages: Go, Shell, CUE

Score: 38 / 100              Verdict: Not ready (critical checks FN-1.1, FN-1.2, FN-8.1 failed)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           2 of 2
4 Monitoring                 1 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 1 of 2
8 Security                   0 of 2

Fix first
1. FN-1.1  Production runs one instance (HPA min = replicaCount 1); no zone spread, no PDB
           charts/podinfo/values-prod.yaml:4
2. FN-1.2  Redis client dials with no explicit connect/read/write timeout
           pkg/api/http/cache.go:140
3. FN-8.1  Hardcoded default JWT signing secret in code
           cmd/podinfo/main.go:90
4. FN-6.1  Image with no tag (implicit latest) in a deployed Helm test pod
           charts/podinfo/templates/tests/jwt.yaml:17
5. FN-6.2  Rolling update allows maxUnavailable 1 with 1 production replica; no rollback step or runbook
           charts/podinfo/templates/deployment.yaml:24

No evidence: FN-2.1 no SLO file or written availability/latency target; FN-2.2 no burn-rate alert rules; FN-4.2 JSON logs present but no alert rule files in repo
```

### Run 2 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: stefanprodan/podinfo   Commit: f72ffd4
Platform: Kubernetes         Languages: Go, Shell, CUE

Score: 31 / 100              Verdict: Not ready (critical checks FN-1.1, FN-1.2 and FN-8.1 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           2 of 2
4 Monitoring                 1 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   0 of 2

Fix first
1. FN-1.1  Backend autoscaler minimum is 1, with no PodDisruptionBudget or zone spread in the production overlay
               deploy/bases/backend/hpa.yaml:10
2. FN-1.2  Redis client is dialed with no connect, read or write timeout
               pkg/api/http/cache.go:140
3. FN-8.1  Hard-coded default JWT signing secret in code
               cmd/podinfo/main.go:90
4. FN-6.1  Helm test pod image has no tag
               charts/podinfo/templates/tests/jwt.yaml:17
5. FN-7.1  nodePort is set in production values while the Service type is ClusterIP, so nothing uses it
               charts/podinfo/values-prod.yaml:39

No evidence: FN-2.1 (no SLO file or written availability and latency target in the repo); FN-2.2 (no burn-rate alert rules in the repo); FN-4.2 (logs are JSON at cmd/podinfo/main.go:255, but the repo has no alert rule files); FN-6.2 (rolling update with maxUnavailable 0 and revision history exist, but the production overlay has no documented or automated rollback; README.md:266 only links to Flux remediation)
```

### Hand check (draft 1)

Every cited file and line was opened and read. The FN-8.1 secret value was not copied.

- **FN-6.2 disagrees.** Run 1 is Fail, run 2 is No evidence. The repository has two production paths:
  - The Helm `values-prod.yaml` runs one replica, and `charts/podinfo/templates/deployment.yaml:24` sets maxUnavailable 1.
  - The Kustomize production overlay sets maxUnavailable 0 and revisionHistoryLimit 5 (`deploy/bases/frontend/deployment.yaml:7-11`).

  Neither path defines a rollback step. Run 1 judged the weaker path, run 2 the stronger one.
- **FN-7.1 disagrees.** Run 1 is Pass, run 2 is Fail. Both are factually right. Container ports match the listen flags (`deploy/bases/frontend/deployment.yaml:30-39`). `nodePort: 31198` in `charts/podinfo/values-prod.yaml:39` is never rendered, because the production Service type is ClusterIP (`charts/podinfo/templates/service.yaml:23`). The standard does not say whether an unused key in a values file counts as dead config.
- **FN-1.1 has one wrong citation.** Run 1's supporting citation `charts/podinfo/templates/hpa.yaml:276` does not exist, because the file has 42 lines; the right line is 14. The finding itself holds: production values enable the HPA with minimum `replicaCount: 1` (`charts/podinfo/values-prod.yaml:4`), and the PDB renders only when replicaCount is above 1 (`charts/podinfo/templates/pdb.yaml:1`).
- **FN-6.1 is borderline.** Both runs failed it on a Helm test hook image (`charts/podinfo/templates/tests/jwt.yaml:17`). That meets the evidence line ("every image reference in manifests"). It is not a production workload, though: the production images in `deploy/bases` are all pinned.
- **Every other finding was verified.**
