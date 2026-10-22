# M2 plan: application and deployment fact extraction

Status: approved with conditions by the CEO on 2026-10-21 (DECISIONS.md M2-D1 to M2-D4, requirements M2-R1 to M2-R3). M1 was merged to `main` as `d58bcbe` on 2026-10-21. Amendments requested in M2-0 (`docs/fifthnine/M2-0.md`, "Amendments to the M2 plan requested") apply once approved; until then this text is the approved plan.

M2 turns the M1 inventory into resolved, source-located facts. It adds no rules. Scans still report `INCOMPLETE` with `COV-NO-RULES`, and the result schema stays `fifthnine.result/v1` (revision 2 unless the CEO approves the M2-0 revision 3 proposal): `Fact`, `FactStatus`, `OverrideExposure`, `SourceRef` and `Linkage` already carry everything below. If implementation finds a gap in the schema, the change comes to the CEO before it is made.

Contract gate (section 9): semantic fixtures pass, provenance is accurate, and conflicting or missing profiles return unknown. Demo: derived facts with exact file and line on the two supported sample services.

## Scope

In scope, all within one repository:

1. Spring Boot effective configuration for the subset in ARCHITECTURE.md: packaged `application.*` and `application-{profile}.*`, multi-document files with `spring.config.activate.on-profile`, literal profile groups, `.properties` over `.yml` in one location, later profiles over earlier ones, `${...}` placeholders with defaults, relaxed binding from environment variables, `JAVA_TOOL_OPTIONS` system properties, `SPRING_APPLICATION_JSON`, and command-line arguments from Kubernetes `args`.
2. Active profile resolution from, in order of authority: `--profile`, `.fifthnine.yml`, Deployment `env`, Dockerfile `ENV`. Conflicts are recorded as coverage notices. No signal, or a signal that could come from outside the repository, gives the base profile plus an assumption, never a guessed profile (RULES.md F4, F5, F8).
3. Python application configuration: the FastAPI app object that the effective container command loads (`module:attr`), `FastAPI(debug=...)` literals, route paths used by probes, uvicorn and gunicorn command-line flags, `gunicorn.conf.py` read with `ast` and literal values only, and pydantic-settings fields with their env binding and defaults. Repository code is never imported or executed.
4. Java source facts needed by the first rule wave: HTTP client construction and timeout settings (RestTemplate, RestClient, WebClient, JDK `HttpClient`), Resilience4j annotations, and `SpringApplication.setAdditionalProfiles`. Syntactic only, using an AST parser; no type resolution.
5. Framework-version semantics: a versioned defaults table (`resolve/defaults/*.yaml`) for each setting the first wave reads. Every entry cites a release tag, file and line. A `default` fact is produced only when the component version is resolved and the table has an entry for it; otherwise the fact is `unresolved` with the reason. Behaviour changes between versions, such as the 3.4 actuator `access` model, are table entries, not code branches.
6. Docker and Kubernetes relationships: Dockerfile stages, `ENV`, `EXPOSE`, `WORKDIR`, `ENTRYPOINT` and `CMD` (exec and shell form), and `COPY --from`; the in-repository object graph (Deployment to Service by selector, Ingress to Service backend, HPA `scaleTargetRef`, PDB selector, ConfigMap and Secret references from `env`, `envFrom` and volumes); the effective container command (Kubernetes `command`/`args` over Dockerfile `ENTRYPOINT`/`CMD`); and override exposure for each mechanism X1 to X11 in RULES.md.
7. Service linkage proposals (E4) and the `fifthnine links` and `fifthnine links accept` commands frozen in CLI.md, with the D3 safeguards: explicit confirmation, provenance, and an evidence digest that marks a link `stale` when its evidence changes. `scan` never accepts a link.
8. Source provenance: every fact carries a repository-relative path, line range and column where the parser gives one, and a redacted excerpt. Facts appear in JSON with `--facts all`, and the console gets a short facts summary.

Out of scope, unchanged from the locked Phase 1: any rule or verdict (M3), diff mode, SARIF and baselines (M4), Helm and Kustomize rendering (E3), cross-repository or application-wide graphs, AI agents, MCP, FifthNine Verify, and anything in Phase 2.

## Work packages and delivery

Each package lands as its own pull request into `main`, merged only with CEO approval. Each carries its fixtures, provenance tests and docs.

| PR | Package | Depends on |
|---|---|---|
| M2-0 | Dev corpus list (A14, M0-D8), fact answer keys for both samples, fixture layout | none |
| M2-1 | Dockerfile model, Kubernetes object graph, effective container command, override exposure | M2-0 |
| M2-2 | Spring effective configuration and profile resolution; versioned defaults table for Spring Boot | M2-1 |
| M2-3 | Python application configuration; defaults for uvicorn, gunicorn, FastAPI | M2-1 |
| M2-4 | Java source facts (AST) | M2-2 |
| M2-5 | Linkage proposals, `links`, `links accept`, stale detection; facts output and console summary; corpus measurement | M2-1 to M2-4 |

The contract budgets one week for M2. That is not realistic for this scope at the quality M1 reached. My estimate is two to three weeks, with M2-1 first because both configuration resolvers need the effective container environment.

## Acceptance criteria

The M2 gate passes when every item below holds on `main` with CI green.

1. Semantic fixtures pass. Every resolution row in RULES.md that M2 is responsible for has a fixture: profile matrix F1 to F14 (the profile and value each row resolves, not its verdict), external override rows X1 to X11 (the fact status and override exposure each produces), and the probe path rows of FN-DEP-002, including the R4 and R6 management-path edge cases.
2. Provenance is accurate. An automated oracle re-reads every emitted fact's source line from disk and checks that the value or key it claims is on that line. It runs on both samples and on every dev corpus repository. Target: zero mismatches.
3. No invented values. Each fact in the hand-labelled answer keys for both samples is checked for value, status and location. A fact whose value cannot be determined must come out `unresolved` with a reason; a wrong resolved value fails the gate even when the scan reports it with confidence. Target on the samples: every labelled fact correct. On the dev corpus, extraction misses are listed per repository, not hidden in an average.
4. Missing or conflicting profiles give unknown. F4, F5 and F8 behave as specified, and no fact is attributed to a profile the repository does not activate.
5. Defaults are cited or absent. Every `default` fact carries `default_ref` with tag, file and line, and a resolved component version. A defaults lookup for an unresolved or unsupported version produces `unresolved`, never the nearest version's default.
6. Linkage follows D3. Proposals are `inferred`; `links accept` writes nothing without confirmation, records provenance and digest, and a changed Dockerfile, image, command, args, ports or build identity marks the link `stale`.
7. M1 guarantees still hold: byte-identical JSON across runs and paths, no network (sockets blocked in tests, `unshare --net` in CI), exit codes, and the untrusted-input limits. New parsers get the same hostile-input tests M1 has (deep nesting, oversized files, aliases, malformed input).
8. Performance is measured: a generated 2,000-file repository scans in under 60 seconds and under 500 MB RSS on a CI runner (ARCHITECTURE.md targets).
9. Condition 4 measurement: `summary.assessment_limits` is populated and reported per dev corpus repository (linkages declared, inferred and unresolved; facts unresolved by cause).
10. Independent review. A reviewer that has not seen the implementation reviews each PR before it is presented for merge, as for M1.
11. Demo: `fifthnine scan samples/orders-service-spring --facts all` and the same for `catalog-api-fastapi` show the derived facts with exact file and line, and the answer-key comparison is attached to the M2 report.

## Known limitations at M2 exit

These are design limits, documented in advance, not defects.

- `spring.config.import`, Spring Cloud Config, Spring Cloud Kubernetes, Vault and out-of-repository mounted files are not read; affected keys are `unresolved` or carry `file_possible`.
- Values set in Java code, `@ConfigurationProperties` field defaults and `@Value` defaults outside configuration files are not resolved in M2.
- Java facts are syntactic. A timeout set through a helper method, a bean defined in another class, or a builder passed across methods is `unresolved`.
- Python facts come from literals only. Settings computed at import time, read from files, or built by factories are `unresolved`.
- Helm and Kustomize sources are still not rendered (E3); teams scan rendered output.
- Gradle computed versions and Maven parent inheritance stay as in M1, so some defaults lookups will be `unresolved` for want of a resolved version.
- The support matrix still lists no tested version, so every in-range version stays `untested`. Spring runtime fixtures that would make a version `tested` depend on decision 3 below.
- Linkage covers one repository. Images built in another repository, or images without a matching Dockerfile, stay `unresolved`.
- Kotlin source and WebFlux functional endpoints are not parsed.

## Decisions requested

1. Approve this plan, the PR sequence and the acceptance criteria.
2. Java parser dependency. I propose `tree-sitter` with `tree-sitter-java`: maintained, prebuilt wheels for Linux, macOS and Windows, no JVM, works offline, MIT licensed. The alternative, `javalang`, is pure Python but unmaintained and does not parse modern Java syntax such as records and switch expressions. This adds a compiled dependency to the package.
3. Spring runtime fixtures (M0 README L2). GitHub-hosted runners can reach Maven Central. I propose a separate CI job, allowed network access, that builds and runs small Spring Boot fixture apps to confirm the defaults table and the R4 and R6 edge cases. The scanner itself never uses the network, and the offline smoke job stays as it is. This is the path to the first `tested` versions.
4. Dev corpus. M2-0 publishes about 20 public Spring Boot and FastAPI repositories with in-repository manifests, with licences recorded, before the independent benchmark author starts selecting held-out cases (A14). I will send the list for approval with that PR.
