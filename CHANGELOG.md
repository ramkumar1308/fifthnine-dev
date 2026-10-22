# Changelog

## 0.1.0.dev0 (unreleased): M2-1 deployment facts

- Result schema revision 3 within `fifthnine.result/v1`: optional `inputs`, `exposure_sources`, `link_ids`, `precedence` and `absent` on facts, and `upstream` citations on defaults. Revision 2 documents stay valid.
- Dockerfile facts (stages, base image, `WORKDIR`, `EXPOSE`, `ENV`, `ENTRYPOINT`, `CMD`, `COPY --from`, `USER`) with `ARG` and `ENV` substitution; default target and unresolved build target kept apart.
- Kubernetes facts for Deployment, Service, Ingress, HPA, PDB and ConfigMap, with selector, namespace and port relationships; unset namespaces and unresolved selectors stay unresolved.
- Kubernetes defaults table, each entry cited at v1.37.1 and verified at four releases from 1.34 to 1.37.
- Container environment model with override exposure, effective container command, and container-to-source link proposals under the unchanged evidence rule.
- `--facts all` emits these facts and links; the console shows a deployment summary.
- Dev dependency `dockerfile==3.4.0` (BuildKit parser) for the provenance oracle; no new runtime dependency.

## 0.1.0.dev0 (unreleased): M1 CLI foundation

- Installable `fifthnine` package and console script (`pip install .`, `pipx install .`, `python -m fifthnine`).
- `fifthnine scan [PATH]` with deterministic console and JSON output on the frozen `fifthnine.result/v1` schema (revision 2).
- Repository discovery: sorted walk, root `.gitignore` and `ignore_paths`, built-in exclusions, 1 MiB per-file cap for parsed files, 20,000-file limit, symlinks never followed, binary detection.
- Hardened parsers: XML with DOCTYPE/entity declarations refused, YAML via ruamel round-trip loader (no object construction), TOML via tomllib/tomli.
- Stack and version detection for Spring Boot, Java, Resilience4j, Spring Framework, Python, FastAPI, tenacity, httpx, gunicorn, uvicorn and Kubernetes object kinds.
- Tested / untested / unsupported classification from the packaged support matrix (no version tested yet).
- Status is always `INCOMPLETE` with a blocking `COV-NO-RULES` notice: no reliability rules exist in this build.
- Exit codes 0/1/2; flags and commands reserved for later milestones exit 2 with the milestone named.
- `.fifthnine.yml` loading and validation (profile, policy, ignore paths, service declarations).
- `fifthnine schema result|config|baseline|support-matrix`, `fifthnine rules list`.
- Review fixes before merge: YAML alias bombs refused; Kubernetes YAML composed with libyaml without alias expansion, non-Kubernetes YAML not parsed; deep TOML/YAML nesting reported per file; secrets redacted from parse errors; requirements continuations, hashes and URLs; line provenance scoped to the right TOML section; Gradle comments, `jvmToolchain` and Java 1.8; Maven qualifiers compared numerically; `.gitignore` and `ignore_paths` exclusions reported; unreadable files, roots and non-UTF-8 names handled; UTF-8 output on Windows code pages; unexpected errors exit 2; report file excluded from its own scan; CI tool versions pinned.
- CI: lint, type check and tests on Python 3.10-3.13 (Linux) plus macOS and Windows on 3.12; wheel build, clean install and networking-disabled smoke scans.
