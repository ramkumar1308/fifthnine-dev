# FifthNine scorecard: reference-service

- Folder: `examples/reference-service`, scored as one service inside fifthnine-dev. Its pipeline is `.github/workflows/reference-service.yml` at the repository root.
- Commit: 17b5d30
- Standard: 1.0 (current) and draft 2 (kept below)
- Runs: two per version, each in a fresh agent context, on 22 November 2026. In each version the two runs agree on all 16 results.

## Version 1.0

### Run 1

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

### Run 2

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
FN-1.1  Pass  examples/reference-service/deploy/base/deployment.yaml:8 (replicas 3; zone spread :35; PDB minAvailable 2 deploy/base/pdb.yaml:8)
FN-1.2  Pass  examples/reference-service/service.py:117 (timeout on every attempt; attempts capped :54, :110; backoff with jitter :131)
FN-2.1  Pass  examples/reference-service/slo/slos.yaml:30 (99.9% over 30d :10; latency 99% within 300 ms :37, :61)
FN-2.2  Pass  examples/reference-service/deploy/base/prometheusrule.yaml:34 (multi-window burn rate, fast and slow, :34-:55)
FN-3.1  Pass  examples/reference-service/deploy/base/hpa.yaml:12 (min 3, max 12 :13, CPU metric :17)
FN-3.2  Pass  examples/reference-service/deploy/base/deployment.yaml:76 (liveness /healthz, readiness /readyz :83; served at service.py:159, :162)
FN-4.1  Pass  examples/reference-service/service.py:28 (duration :28, count :34, errors :35, in-flight vs limit :36-:37; /metrics :166; ServiceMonitor deploy/base/servicemonitor.yaml:13)
FN-4.2  Pass  examples/reference-service/service.py:70 (JSON formatter with request_id :76; alert rules deploy/base/prometheusrule.yaml:31)
FN-5.1  Pass  examples/reference-service/deploy/overlays/production/kustomization.yaml:6 (Kustomize overlay synced by deploy/argocd-application.yaml:13)
FN-5.2  Pass  .github/workflows/reference-service.yml:36 (on pull_request :4 and push :8; unit tests :36; image build :38)
FN-6.1  Pass  examples/reference-service/deploy/base/deployment.yaml:48 (image 1.0.0; base image examples/reference-service/Dockerfile:1)
FN-6.2  Pass  examples/reference-service/deploy/base/deployment.yaml:14 (RollingUpdate maxUnavailable 0; revisionHistoryLimit :9; rollback examples/reference-service/RUNBOOK.md:9)
FN-7.1  Pass  examples/reference-service/deploy/base/deployment.yaml:51 (port 8080 = PORT read at service.py:60; all env vars :53-:65 read at service.py:53-:66)
FN-7.2  Pass  examples/reference-service/deploy/base/deployment.yaml:56 (LOG_LEVEL INFO; plain entrypoint Dockerfile:15; sole dependency requirements.txt:1 imported at service.py:24)
FN-8.1  Pass  examples/reference-service/deploy/base/deployment.yaml:67 (token from secretKeyRef, sourced from ExternalSecret deploy/base/externalsecret.yaml:11)
FN-8.2  Pass  examples/reference-service/deploy/base/deployment.yaml:28 (runAsNonRoot; no privilege escalation :97; drop ALL :101; Dockerfile USER 10001 :12)
```

### Hand check

Every cited line was opened and read, and each one supports its result.

- **Pass mark met.** Both runs score 16 of 16 and agree on all 16 results. They differ only in which supporting lines they list.
- **Pipeline.** FN-5.2 now rests on `.github/workflows/reference-service.yml` at the repository root. GitHub runs it on pull requests and pushes to main that change this folder or the workflow. The workflow has not run yet, because nothing has been pushed.
- **Tests.** The 14 unit tests pass locally on Python 3.14 with `prometheus-client==0.21.1`.
- **Image build not tested locally.** The Docker daemon was not running on the machine used. The new workflow builds the image in CI.
- **Notes the runs raised.**
  - Run 1 noted that FN-6.1 excludes example folders, and this service lives under `examples/`. It scored the service because it was told to.
  - Both runs noted the dummy token in `tests/test_service.py:14`, which points at a `.invalid` host, and treated it as test data.
  - Run 2 noted that the Argo CD Application's `repoURL` (`deploy/argocd-application.yaml:11`) is a placeholder, not this repository.

## Draft 2

Commit 5edc393. In draft 2 the folder was scored as its own repository root, and its pipeline sat inside the folder.

### Run 1 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: fifthnine-dev/examples/reference-service   Commit: 5edc393
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
FN-1.1  Pass  deploy/base/deployment.yaml:8
FN-1.2  Pass  service.py:117
FN-2.1  Pass  slo/slos.yaml:30
FN-2.2  Pass  deploy/base/prometheusrule.yaml:34
FN-3.1  Pass  deploy/base/hpa.yaml:12
FN-3.2  Pass  deploy/base/deployment.yaml:76
FN-4.1  Pass  service.py:28
FN-4.2  Pass  service.py:70
FN-5.1  Pass  deploy/overlays/production/kustomization.yaml:4
FN-5.2  Pass  .github/workflows/ci.yml:22
FN-6.1  Pass  deploy/base/deployment.yaml:48
FN-6.2  Pass  deploy/base/deployment.yaml:14
FN-7.1  Pass  deploy/base/deployment.yaml:51
FN-7.2  Pass  requirements.txt:1
FN-8.1  Pass  deploy/base/deployment.yaml:67
FN-8.2  Pass  deploy/base/deployment.yaml:28
```

### Run 2 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: fifthnine-dev/examples/reference-service   Commit: 5edc393
Platform: Kubernetes         Languages: Python, YAML

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
FN-1.1  Pass  deploy/base/deployment.yaml:8
FN-1.2  Pass  service.py:117
FN-2.1  Pass  slo/slos.yaml:30
FN-2.2  Pass  deploy/base/prometheusrule.yaml:34
FN-3.1  Pass  deploy/base/hpa.yaml:12
FN-3.2  Pass  deploy/base/deployment.yaml:76
FN-4.1  Pass  service.py:36
FN-4.2  Pass  service.py:70
FN-5.1  Pass  deploy/overlays/production/kustomization.yaml:4
FN-5.2  Pass  .github/workflows/ci.yml:22
FN-6.1  Pass  deploy/base/deployment.yaml:48
FN-6.2  Pass  deploy/base/deployment.yaml:14
FN-7.1  Pass  deploy/base/deployment.yaml:51
FN-7.2  Pass  requirements.txt:1
FN-8.1  Pass  deploy/base/deployment.yaml:67
FN-8.2  Pass  deploy/base/deployment.yaml:28
```

### Hand check (draft 2)

Every cited line was opened and read, and each one supports its result.

- **Pipeline.** Both runs noted that `.github/workflows/ci.yml` runs only when this folder is the root of a repository. Inside fifthnine-dev, GitHub does not run it.
- **Tests.** The 14 unit tests pass locally on Python 3.14 with `prometheus-client==0.21.1`.
- **Manifests.** The production overlay renders with `kubectl kustomize`.
- **Image build not tested.** The Docker daemon was not running on the machine used, so the image build has not been run.
