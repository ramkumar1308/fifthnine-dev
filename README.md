# FifthNine Scan

Local-first, evidence-backed production-readiness review for application repositories. FifthNine reads code, configuration and Kubernetes manifests in a repository and reports reliability findings with file and line evidence, plus an explicit statement of what it could not assess.

**Status: M1 (CLI foundation). This build contains no reliability rules.** It installs, walks a repository safely, detects supported frameworks and versions, classifies them as tested, untested or unsupported, and reports `INCOMPLETE` with an honest coverage section. It does not assess production readiness yet. The first rules arrive in M3.

FifthNine does not certify production readiness and does not prevent outages. A scan with no findings is not a statement that a system is production ready.

## Install

From a clone (no PyPI release yet):

```
pipx install .
# or
python -m pip install .
```

Requires Python 3.10 to 3.13. No account, API key, network access or LLM is used.

## Use

```
fifthnine scan .
fifthnine scan . --format json --output fifthnine.json
fifthnine scan . --format json --facts all      # include detection facts with file:line provenance
fifthnine scan . --profile prod                 # recorded; profile resolution starts in M2
fifthnine rules list
fifthnine schema result                          # frozen JSON Schema for the report
fifthnine --version
```

Exit codes: `0` scan completed and nothing is policy-blocking (always, in this build); `1` gate mode with a blocking finding (cannot happen without rules); `2` execution or configuration error, including flags reserved for later milestones (`--base-ref`, `--baseline`, `--format sarif`, `--timing`, `links`, `baseline create`).

## What M1 detects

| Component | Sources read |
|---|---|
| Spring Boot | Maven parent or BOM import (with `${property}` resolution in the same POM), Gradle plugin literal, Gradle version catalog |
| Java | Maven `java.version` / `maven.compiler.*`, Gradle toolchain or compatibility literal, JDK base images in Dockerfiles |
| Resilience4j, Spring Framework | Explicit Maven versions |
| Python | `.python-version`, `python:` base images in Dockerfiles |
| FastAPI, tenacity, httpx, gunicorn, uvicorn | `uv.lock`, `poetry.lock`, exact pins in `pyproject.toml` and `requirements*.txt` (ranges are reported as "version not determined") |
| Kubernetes | `apiVersion`/`kind` of every object in plain YAML; Helm templates and Kustomize overlays are detected and reported as not rendered |

Support levels come from `src/fifthnine/data/support_matrix.v1.yaml`. No version is tested yet, so everything inside the approved ranges is reported as `untested`.

Full details, limits and acceptance evidence: [`docs/fifthnine/M1.md`](docs/fifthnine/M1.md). Design and decisions: [`docs/fifthnine/`](docs/fifthnine/).

## Running alongside other scanners

FifthNine does not repeat security or IaC checks. Keep running Checkov or kube-linter for Kubernetes posture, Hadolint for Dockerfiles, Ruff (`S113`) or Bandit for Python HTTP timeouts, gitleaks or Trivy for secrets, and Squawk or Atlas for migrations.

## Development

```
python -m pip install -e ".[dev]"
ruff check src tests scripts
mypy
python -m pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
