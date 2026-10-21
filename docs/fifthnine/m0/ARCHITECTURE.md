# FifthNine Scan architecture (final for M0 approval)

Scope: Phase 1, v0.1. One local engine used by `fifthnine scan .` and by CI. No services, no database, no network, no execution of repository code, no LLM.

## Pipeline

```
discover   walk PATH; .gitignore + built-in excludes (.git, node_modules, venvs, target/, build/);
           size cap; symlinks resolved and rejected if outside root; binary sniffing
   |
detect     classify: pom.xml, build.gradle(.kts), pyproject/requirements/lockfiles, application*.{yml,yaml,properties},
           *.java, *.py, Dockerfile*, Kubernetes YAML (apiVersion + kind), gunicorn.conf.py, .env
   |
extract    per-format extractors -> Facts with SourceRef and status. Extractors hold no rule logic
   |
resolve    1. stack and version detection (Boot from parent/BOM/plugin literal; Python pins)
           2. support level per detected version from support/matrix.v1.yaml (tested | untested | unsupported);
              unsupported components get a COV-UNSUPPORTED notice and their rules are not run
           3. version-defaults table (data file, every entry cites tag + file + line)
           4. Kubernetes object graph (Deployment <-> Service <-> Ingress <-> HPA <-> PDB by name/selector)
           5. effective container command (Dockerfile ENTRYPOINT/CMD replaced by k8s command/args)
           6. service linkage: proposals, plus digest check of declared links (declared | stale)
           7. Spring effective config: profiles, precedence, relaxed binding, override exposure
   |
evaluate   registered rules compute check outcomes from resolved Facts; derive_verdict() assigns
           verdict and assumptions (D2, D9)
   |
verify     dedupe, fingerprint, blocking eligibility, baseline and suppression status, coverage notices,
           assessment-limit counters, status derivation
   |
report     console | JSON (fifthnine.result/v1) | SARIF 2.1.0
```

Each stage is a pure function of its inputs. Output ordering is sorted by (rule_id, subject.key). No timestamps unless `--timing`. Two scans of the same tree produce byte-identical JSON; M1's gate tests this.

## Package layout

```
src/fifthnine/
  cli.py
  schema/        v1.py, config_v1.py            (frozen at M0)
  core/          workspace.py  engine.py  registry.py  fingerprints.py  status.py
  extract/       java_ast.py (tree-sitter-java)  python_ast.py (stdlib ast)  yaml_k8s.py (ruamel.yaml)
                 dockerfile.py  spring_config.py  properties.py  build_files.py  gunicorn_conf.py
  resolve/       versions.py  defaults/ (spring_boot.yaml, python.yaml, kubernetes.yaml)
                 k8s_graph.py  container_command.py  linkage.py  spring_env.py  overrides.py
  rules/         resilience/  capacity/  deployment/  production_config/  network/
  output/        console.py  json_report.py  sarif.py
  policy/        baseline.py  suppressions.py  gate.py
tests/           unit/  fixtures/<RULE_ID>/<row-id>/  integration/  e2e/
samples/         orders-service-spring/  catalog-api-fastapi/
benchmarks/dev/  answer keys and runtime checks for samples (never held-out data)
```

## Fact model

Defined in `schema/v1.py`. Two fields carry the trust model:

`status`: `declared`, `default` (needs a resolved version and a citation), `overridden`, `inferred`, `unresolved`.

`override_exposure`: `none`, `env_possible`, `file_possible` (see RULES.md "External override handling").

Rules compute a check outcome (`fail`, `pass`, `indeterminate`) from facts. They never assign the verdict themselves: `derive_verdict()` in `schema/v1.py` does, and the `Finding` model validators reject any document that breaks its invariants. A material input with no value in the repository gives UNKNOWN. A material input that exists but could be replaced from outside the repository (`env_possible`, `file_possible`), an inferred or stale linkage, a rule-supplied context assumption (classpath, admission defaults, GitOps behaviour, routing) or an untested component version makes a failing check CONDITIONAL; the computation and `verdict_if_assumptions_hold` stay in the finding. A team declaration in `.fifthnine.yml` turns the matching assumption into `declared_by_team`, and the finding cites the declaration's location.

## Spring effective configuration (supported subset)

Precedence implemented, lowest to highest, following the Spring Boot reference "Externalized Configuration" order: packaged `application.*`, packaged `application-{profile}.*`, `file:./` and `file:./config/` copies when the source is in the repo (ConfigMap mounted at WORKDIR), OS environment (Deployment `env`, in-repo `envFrom`, Dockerfile `ENV`) with relaxed binding, Java system properties from `JAVA_TOOL_OPTIONS`, `SPRING_APPLICATION_JSON`, command-line arguments in k8s `args`. Within one location `.properties` beats `.yml`. Later active profiles beat earlier ones. Literal profile groups expand.

Not supported, reported as UNKNOWN with a reason: `spring.config.import`, Spring Cloud Config and Spring Cloud Kubernetes config, Vault, out-of-repo mounted files, values set in Java code, `@ConfigurationProperties` defaults declared in application code, WebFlux functional endpoints for probe paths.

## Service linkage (E4)

A rule that combines an application fact with a workload fact needs a link between the workload container and the source that built its image. FifthNine proposes links from evidence:

- exactly one Dockerfile whose runtime stage copies artifacts of exactly one app module;
- exactly one workload container whose image repository basename matches the module name (Maven `artifactId`, `pyproject` name) or whose command names a module present in the repo (`app.main:app`);
- matching ports between Dockerfile `EXPOSE`, app config and `containerPort`.

Proposals are `inferred` and advisory. `fifthnine links accept` turns a proposal into a declared link, with these safeguards (D3):

- Explicit approval. The command prints the exact YAML and the evidence, and writes nothing until a person confirms. Without a TTY it refuses unless `--yes` is given; `fifthnine scan` never accepts links on its own. The written file is meant to go through normal code review, so git history records who approved it.
- Provenance. Each accepted entry records `accepted_by: links-accept`, `accepted_on` and the evidence lines shown at approval. Results report the link's `declared_in` location, who accepted it and when.
- Invalidation. Each accepted entry stores `evidence_digest`, a sha256 over the normalized evidence: the Dockerfile, the workload container's image repository, command, args and ports, and the build descriptor identity (Maven `artifactId`, `pyproject` name). Every scan recomputes it. On mismatch the link is `stale`, is treated like an inferred link, and a coverage notice asks for re-acceptance. Hand-written entries (`accepted_by: user`) have no digest and are trusted as written, which the result states.

A team never has to write the mapping by hand in the common single-service case.

## Rules

A rule is a Python class registered under the `fifthnine.rules` entry-point group:

```python
class Rule(Protocol):
    id: str                      # FN-DEP-003
    version: str                 # semver of the rule logic
    domain: Domain
    blocking_capable: bool       # False for advisory-only rules (FN-CAP-002b)
    applies_to: StackSelector    # e.g. spring-boot >=3.2,<3.6 + k8s Deployment
    required_facts: list[FactQuery]
    def evaluate(self, ctx: ResolvedContext) -> Iterable[Finding]: ...
```

Each rule ships `tests/fixtures/<RULE_ID>/<row-id>/` for every row of its matrix in RULES.md, plus a docs page used by `rules explain`. Third-party rule packs install as Python packages using the same entry point.

Promotion to blocking (D5). Every rule starts with `blocking_capable = False`. A rule is promoted only when, for each tested version it depends on, its runtime fixtures and boundary-case fixtures (the matrix rows marked as boundaries, such as RULES.md D3, R4, R6) pass in CI, and the outside SRE reviewer has accepted its findings on the dev corpus. Promotion is recorded in DECISIONS.md.

## Outputs

JSON validates against `schemas/result.v1.json`. SARIF 2.1.0 maps findings to results and coverage to tool execution notifications. Console is for humans and is never parsed.

## Diff mode

HEAD and `--base-ref` are both parsed from git objects (`git show <ref>:<path>`), never checked out and never built. Both are evaluated with full-repository context. Fingerprints (`sha256(rule_id + subject.key)`) classify each finding as new, existing or fixed. If the base ref is missing or shallow, the scan falls back to full mode and says so; it never reports "no new findings" from a failed diff.

## Baselines and suppressions

Baseline: fingerprints only, no timestamps, committed by the team. Suppression: in `.fifthnine.yml`, requires a reason of at least 10 characters and an expiry date. Expired suppressions are reported as `suppression_expired` and the finding counts again.

## AI separation (CEO condition 5)

The v0.1 engine has no AI code path. The result schema reserves a top-level `advisory` section that is always `{"enabled": false, "items": []}` in v0.1 and has a hard `max_length=0` in the v1 model. A future advisory mode would need a schema change approved by the CEO, would populate only that section, could never edit `findings`, and could never make a scan exit non-zero.

## Untrusted input

Repositories are hostile input. Defaults: 1 MiB per file, 20,000 files, YAML alias expansion capped, YAML documents per file capped, parse timeout per file where the parser allows it, symlinks outside root rejected, no `eval` or import of repository Python (AST only), no shelling out except `git show` / `git diff` with argument lists, secret-like values (`password`, `secret`, `token`, `key` in the key name) redacted in excerpts.

## Performance targets for M1/M2 measurement

Full scan of a 2,000-file repository under 60 seconds on a CI runner, under 500 MB RSS. Measured and reported, not assumed.

## Condition 4 instrumentation

Every result carries `summary.assessment_limits`: linkages declared, inferred and unresolved; findings capped by linkage; findings carrying an override assumption; UNKNOWNs from unsupported config versus unresolved values; rules not assessable. M1 and M2 run FifthNine on a dev corpus and report these counts per repository.
