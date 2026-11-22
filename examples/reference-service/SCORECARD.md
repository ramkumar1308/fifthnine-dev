# FifthNine scorecard: reference-service

- Folder: `examples/reference-service`, scored as its own repository root
- Commit: 5edc393
- Standard: draft 2
- Runs: two, each in a fresh agent context, on 22 November 2026. The runs agree on all 16 results.

## Run 1

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

## Run 2

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

## Hand check

Every cited line was opened and read, and each one supports its result.

- **Pipeline.** Both runs noted that `.github/workflows/ci.yml` runs only when this folder is the root of a repository. Inside fifthnine-dev, GitHub does not run it.
- **Tests.** The 14 unit tests pass locally on Python 3.14 with `prometheus-client==0.21.1`.
- **Manifests.** The production overlay renders with `kubectl kustomize`.
- **Image build not tested.** The Docker daemon was not running on the machine used, so the image build has not been run.
