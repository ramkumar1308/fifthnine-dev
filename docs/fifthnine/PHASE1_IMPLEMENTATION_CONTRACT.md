FifthNine Scan — Phase 1 CTO Implementation Contract

Amendments (CEO-approved, 2026-10-21; full record in DECISIONS.md). Section 5 statuses and section 4 verdict semantics are read together with A11, A13 and A15.

A1 (E1) Repository: new independent repository outside SasquatchLabsInc, Apache-2.0. Push access not assumed until confirmed.
A2 (E2) Section 5 FN-DATA-001/002 removed from v0.1. Data reliability remains a future domain; Squawk and Atlas documented as delegates.
A3 (E3) v0.1 scans already-rendered Helm/Kustomize output; native rendering is a later decision based on measured gaps.
A4 (E4) Only declared service linkages can support blocking cross-artifact findings. Inferred linkages are advisory.
A5 (E5) Supported versions: Spring Boot 3.2-3.5, Java 17/21, Python 3.10-3.13.
A6 (E6) Section 5 candidates remain candidates until semantics, applicability, overlap and false-positive cases are validated.
A7 (E7) Independent benchmark author and outside SRE reviewer required before M5 can pass.
A8 (E8) External SARIF aggregation is a stretch goal, not an M4 commitment.
A9 M0 approval conditions C1-C7 (DECISIONS.md) apply to all milestones.
A10 (M0-D1, D4, D6, D7) Approved as proposed: REVIEW_REQUIRED and INCOMPLETE are separate scan statuses; FN-RES-001 is Java-only with Python timeouts delegated; JSON is deterministic by default; Kubernetes scope is Deployment, Service, Ingress, HPA, PDB, ConfigMap.
A11 (M0-D2 revised) When external configuration or cluster-side defaults may override a value material to the verdict, the finding is CONDITIONAL with the repository-derived computation and named assumptions kept, or UNKNOWN when the value cannot be evaluated even under explicit assumptions. VIOLATION requires either no such exposure or a team declaration in .fifthnine.yml, which the finding cites.
A12 (M0-D3) Accepted linkages require explicit human approval, record provenance (who/when/evidence) and an evidence digest, and become stale (non-authoritative) when that evidence changes.
A13 (M0-D5 provisional) No rule may block CI until its runtime and boundary-case tests pass for the tested versions it depends on and the outside SRE reviewer has accepted its findings.
A14 (M0-D8) The development corpus is published before independent held-out selection.
A15 (M0-D9 revised) FifthNine claims support only for versions listed as tested in support/matrix.v1.yaml. Untested versions inside approved ranges are reported as untested and cap failing checks at CONDITIONAL; ranges and limits are documented.
A16 (M0-D10) Package name registration deferred until name and ownership are confirmed.
A17 Sample-specific comparisons with other scanners must not be presented as proven real-world superiority.
A18 M1 delivers a usable CLI foundation only. No M2 work, no Phase 2 capability, no dashboard, application graph or AI agent.

---

Date: 2026-10-21
Status: CEO-approved product direction; proposed engineering plan for CTO review
Owner: Claude (CTO)
Decision authority: Founder/CEO
Product: Open-source, local-first automated SRE production-readiness review of repositories and pull requests.

0. CTO instruction — read first

You are the CTO responsible for building FifthNine Scan, an automated, evidence-backed SRE production-readiness reviewer. The Founder has locked Phase 1. Do not redirect it into a PostgreSQL migration linter, an incident-search product, a system digital twin, a full enterprise service graph, or an AI SRE. Previous FifthNine research is reference material, not a mandate to build its entire architecture.

Mission: Make reliability engineering proactive and automatic in software delivery.
Developer promise: fifthnine scan . produces meaningful, source-evidenced findings about whether the accessible repository follows applicable production reliability engineering principles, plus a clear statement of what could not be assessed.

Two modes, one engine:

1. fifthnine scan . — full-repository production readiness assessment.
2. fifthnine scan . --base-ref <ref> — change-aware PR review, considering surrounding context and new/regressed findings.

Non-negotiables: open source; works offline without an account/API key; no code upload or production credentials; deterministic checks for blocking findings; optional AI only as explicitly marked advisory; honest coverage; reproducible fixtures; no automatic production approval or remediation. Preserve independent held-out benchmark isolation; never inspect or optimize against those cases.

Start by inspecting the repository and current project state. Do not assume existing architecture or overwrite unrelated work. Submit a concise repo audit and design deltas before implementation. Then implement in milestone-sized PRs and report tests and demo evidence at each gate. Product-scope changes need CEO approval.

1. What we learned from Checkov — reuse principles, not code by default

Checkov’s documented design supports: multiple format-specific runners that parse input into definitions; registered checks/policies; CLI file/folder scans; severity/policy selections and skips; custom policies; suppressions; baselines; multiple outputs including JSON, JUnit XML and SARIF; integrations with CI; contributor guidance and pass/fail fixtures per check.

Design FifthNine using those battle-tested patterns. Checkov targets IaC/cloud security and misconfiguration; FifthNine focuses on reliability behavior and production engineering context across code, configuration and deployment. Before implementing a rule, compare its actual scope against Checkov, Trivy, kube-linter, Semgrep, and appropriate specialist tools. Prefer documented interoperability or optional import of existing findings over duplicating their existing security rules.

Reference sources (verify relevant revision and license before reuse):

• https://github.com/bridgecrewio/checkov
• https://github.com/bridgecrewio/checkov/blob/main/CONTRIBUTING.md
• https://www.checkov.io/6.Contribution/Contribution%20New%20IaC%20Runner.html
• https://www.checkov.io/2.Basics/CLI%20Command%20Reference.html
• https://sre.google/sre-book/launch-checklist/
• https://sre.google/sre-book/evolving-sre-engagement-model/

Licensing: Study Checkov’s source for architecture and behavior, but do not copy implementation code or third-party policy text without an explicit license compatibility review and attribution. Do not claim Checkov has zero reliability rules or that FifthNine is unprecedented.

2. Phase 1 product scope

Primary users: application developers, backend engineers, SREs, platform engineers.
Installer: developer (pipx, Python package, or standalone release artifact) or platform team’s shared CI workflow.
Workflow: local scan → fixes → PR report → optional gate after rule precision is established.
No mandatory service catalog, application graph, production telemetry, business journey definitions, or organization server to get useful results.

First-class scan domains

|Domain                        |What to inspect                                                                                                                 |Caveat                                                                      |
|------------------------------|--------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
|Resilience                    |connect/read/request timeouts, retry budget/backoff/jitter, bounded attempts, idempotency clues, failure handling               |not every dependency needs circuit breaking; framework defaults may be valid|
|Capacity                      |resource requests/limits, autoscaling configuration where present, connection pool and queue boundedness, concurrency guardrails|missing HPA in repo is **unknown**, not automatic error                     |
|Deployment safety             |readiness/startup/liveness probes where relevant, rollout capacity settings, graceful termination and shutdown settings         |account for workload type and platform defaults                             |
|Production configuration      |debug/dev profiles, unsafe diagnostics/test switches, production overrides, secrets in config                                   |distinguish intentionally gated nonprod files from deployed profiles        |
|Network exposure              |public administrative ports, broad ingress/egress, plaintext protocol settings                                                  |consider explicit deployment/network context and external tools             |
|Data/integration compatibility|version mismatches, schema/client compatibility where statically provable, migration risks, dependency version conflicts        |no blanket guarantee from manifests alone                                   |
|Failure containment           |unbounded queues, retry loops, missing time budgets, unsafe shutdown semantics                                                  |avoid generic absence-based warnings                                        |

Scope discipline: initial adapters for a supported subset only: (a) Java/Spring Boot, (b) Python/FastAPI/requests/httpx, (c) Kubernetes YAML and Dockerfile, plus common YAML/properties/env config. Select the exact supported combinations after repository audit; do not claim other frameworks are scanned. Terraform can be optional later or via integration rather than building a rival Checkov runner. Aim for 15–20 high-quality rules across these inputs, not hundreds of weak rules. A smaller high-precision subset beats breadth.

3. Architecture

Local repo or CI workspace
  ├── repository / change discovery (ignore, git diff, target profile)
  ├── format detectors and parsers (AST/config parsers; never regex-only for complex semantics)
  ├── normalized facts + provenance (file, line, parser, language, environment)
  ├── bounded context resolver (configs ↔ code ↔ manifests)
  ├── rule registry / applicability engine
  │    ├── deterministic proof checks
  │    ├── conditional concerns / missing evidence
  │    └── optional AI advisory (disabled by default)
  ├── finding verifier, deduplicator and baseline resolver
  └── outputs: CLI, JSON, SARIF, JUnit XML (prioritize CLI + JSON + SARIF)

Recommended implementation: Python CLI with typed domain interfaces, typer or click, Pydantic schemas, parsers suitable for selected languages, pytest, Ruff, mypy; package with pyproject.toml. Final library choice depends on existing repo. Avoid infrastructure/services for Phase 1; do not require PostgreSQL, Redis, SaaS, web UI, or LLM provider.

Keep parsers independent from rules. Register checks with ID, domain, version, severity, applicability, required facts, verdict algorithm, evidence specification, remediation, documentation, tests, and reference. Allow third-party rule packs in documented plugin format. Scan should be deterministic and offline with network disabled.

Normalized fact schema (illustrative)

fact_id: f-123
kind: http_client_timeout
subject: payment_client
value: 3000
unit: ms
scope: production
source:
  path: src/main/java/.../PaymentClient.java
  line_start: 42
  line_end: 46
extractor: spring-http-client-v1
confidence_class: parsed

Do not claim a fact is authoritative if sourced from a non-active profile, unexpanded template, an unresolved variable, or non-executed code. Model the difference between declared, inferred, overridden, and unknown.

4. Finding and result contract

For each finding include:

• stable rule ID, rule version, domain, severity and verdict;
• repository path, relevant lines, framework/target-profile applicability;
• exact observed fact(s) and why they matter, with source/provenance;
• requirement or known semantics being checked, evidence and assumptions;
• actionable remediation and references;
• absence/unknown-data reason, where appropriate;
• new, existing, suppressed status relative to baseline.

Use VIOLATION for an established check failure, CONDITIONAL for dependence on uncertain context, UNKNOWN for unassessable required evidence, and SATISFIED only for a specific supported check with scope and assumptions. Overall scan status is PASS_WITH_COVERAGE, FAIL_VERIFIED, or INCOMPLETE/REVIEW_REQUIRED (policy configured); do not assert production ready solely from a passing scan.

Severity and evidence strength are separate axes. Only high-confidence deterministic violations can be made blocking. Missing files or unsupported frameworks must create coverage notices, not a pass. Do not interpret absence of a circuit breaker, auto-scaler, or alerts as a verified defect absent applicable requirements.

5. First check catalog — candidates, not automatically approved rules

Each must have exact framework/version applicability, counterexamples, tests and comparison to available tools before shipping.

1. FN-RES-001: HTTP client timeout absent where semantics confirm unbounded wait and no effective outer deadline.
2. FN-RES-002: Retry window exceeds a known caller/request deadline, accounting for attempt count and backoff.
3. FN-RES-003: Retries on non-idempotent request without verified deduplication protection → CONDITIONAL unless end-to-end behavior proven.
4. FN-RES-004: Infinite/unbounded retry loop with retryable dependency path.
5. FN-RES-005: Concurrent retries at multiple explicitly known layers create bounded/known amplification.
6. FN-RES-006: Retry-on-error configuration with no backoff in an applicable library (conditional depending on context).
7. FN-CAP-001: Missing Kubernetes CPU/memory requests for a production workload under an applicable policy.
8. FN-CAP-002: Workload limit/requests or autoscaling incompatibility provable from supplied manifests.
9. FN-CAP-003: Unbounded configured queue or connection growth where boundedness is required and behavior known.
10. FN-DEP-001: Production Kubernetes Deployment rollout settings cannot preserve an explicitly stated minimum available capacity.
11. FN-DEP-002: Probe targets mismatched with actual declared listening endpoint, using resolved configuration.
12. FN-DEP-003: Termination grace period shorter than known explicit drain/shutdown budget.
13. FN-CFG-001: Effective production profile enables a known risky debug endpoint or setting.
14. FN-CFG-002: Prod runtime uses documented development/test fallback endpoint unexpectedly (only if active profile is resolved).
15. FN-NET-001: A sensitive admin/debug port publicly exposed by explicitly included deployment/network config.
16. FN-NET-002: Broad public ingress on a port that the project explicitly identifies as administrative.
17. FN-DATA-001: Migration breaks a required consumer schema where consumer/reference evidence is accessible.
18. FN-DATA-002: Version-specific migration/transaction behavior creates a verified risk; delegate to mature specialized scanners if already solved.
19. FN-OPS-001: Missing evidence for an SRE readiness domain → coverage notice, not failure.
20. FN-OPS-002: Production artifact references an unavailable file/manifest or unresolved profile essential for assessment → UNKNOWN.

The CTO should prioritize a few demonstrably differentiated reliability rules, and mark overlapping checks as integration/adaptation opportunities. No bulk copy from Checkov rule catalogs.

6. CLI contract and CI integration

Required commands (design then implement):

fifthnine --version
fifthnine scan .
fifthnine scan . --profile production
fifthnine scan . --base-ref origin/main
fifthnine scan . --format json --output fifthnine.json
fifthnine scan . --format sarif --output fifthnine.sarif
fifthnine rules list
fifthnine rules explain FN-RES-002
fifthnine baseline create --path .
fifthnine scan . --baseline .fifthnine-baseline.json

Start with GitHub Actions reusable workflow example and a generic CI shell example; do not require GitHub-specific server connectivity. Explicit CI exit codes: 0 (no policy-blocking findings), 1 (blocking verified violations), 2 (execution/configuration error). Provide separate summary of conditional/unknown findings in all modes. Set report-only as default for early pilots, with opt-in gate policy. Support repository-local .fifthnine.yml with selected profile, allowed scanners, enable/disable rule, fail threshold, ignore paths, justified suppressions, expiration and file-level rule configuration. Never silently disregard expired suppressions.

Avoid secrets or proprietary code in telemetry/remote requests. CLI must run offline and not send analytics without explicit opt-in. Dependency installation and optional AI are separate from scan execution.

7. How to use AI safely (optional, not MVP prerequisite)

The product must function with no LLM. Explore an optional --ai-review advisory mode only after deterministic engine quality is established. If implemented, AI may identify candidate patterns or missing context but must cite file/line and cannot mark results verified or block CI. Default to local/provider-controlled modes with explicit opt-in, cost and privacy notice; no automatic third-party code transmission. Do not use AI to silently rewrite policy semantics or suppress false positives.

8. Evaluation protocol and benchmark independence

Build a new Phase 1 repository-readiness benchmark; do not reuse the old cross-component held-out cases as if they evaluate the new scope. The previous 36 held-out + 8 journey cases remain sealed for a future Phase 2 evaluation. Independent scenario author holds benchmark private cases; CTO sees only permitted development fixtures.

Include at least two realistic application types in supported stack, with safe and unsafe cases; nonprod-only configurations; framework defaults; inaccessible/unknown configuration; false-positive traps; nested config; resolved profiles; positive and negative fixtures for every rule. Include at least one unfamiliar repository not custom-built for the parser. Ground truth requires documented semantics and independent experienced-SRE review; benchmark tool overlaps with Checkov/Trivy/Semgrep/kube-linter when appropriate and a frontier LLM provided identical repo evidence.

Metrics: per-rule precision and recall with counts and interval; novel true positives beyond baseline union; false alarms per 100 PRs; unknown classification accuracy; source-line correctness; reproducibility across repeated scans; scan time and memory; onboarding time; findings accepted/fixed by SREs. Label speculative findings separately. Do not claim MTTR reduction or production outage prevention from synthetic tests.

Proposed research gates (review before freezing):

• 100% of blocking findings supported by verifiable artifact-level evidence and applicable technical rule semantics.
• No synthetic safe case blocked by unsupported claim in an initial held-out fixture suite (report exact N, not generalized guarantee).
• ≥90% precision on labeled in-scope verified findings in benchmark (report intervals; adjust with expert feedback).
• At least 5 meaningful, independently confirmed SRE reliability findings beyond the selected incumbents / generic LLM baseline across the benchmark; quality beats arbitrary total rule count.
• Scanner remains useful fully offline with no production credentials; routine local scan goal <60s on representative small/mid-size repos (measure sizes), initial onboarding <15 min in a supported repo.
• At least 5 practitioners review results and indicate specific findings they would act on; separately interview at least 5 budget owners/platform leads before any paid platform build.

A result can be technically successful without commercial validation. Define GO/ITERATE/STOP decisions separately for technical quality, adoption and commercial willingness to pay.

9. Implementation milestones (proposed, adjustable after repo audit)

M0 — Audit and freeze contracts (2–3 workdays)

Build: repository inventory; enumerate code and existing work; compile policy comparison matrix; select framework versions and fixtures; lock CLI, fact/finding schemas and policy interfaces; source attribution/licenses.
Demo: proposed fifthnine scan . UX and expected structured result on fixtures (mocked, clearly labeled).
Gate: CEO approves technical scope and supported environments. No major implementation until this gate.

M1 — CLI skeleton and file discovery (week 1)

Build: installable fifthnine CLI; workspace discovery with robust .gitignore/exclusions; profile selection; logging; CLI/JSON outputs; deterministic result schema; exit-code tests; no network calls.
Demo: scan a small repository and report inventory, coverage, unsupported areas, no misleading PASS.
Gate: clean local install, stable schema, reproducible same outputs.

M2 — Parsers and normalized evidence (week 2)

Build: selected language AST/config extractors, YAML/Kubernetes/Dockerfile parsing, source location mapping, config/profile resolution, fact registry. Support source and deployment evidence correlation in a single repo.
Demo: show derived facts with exact file/lines on two supported sample services.
Gate: semantic fixtures pass, provenance accurate, conflicting/missing profiles return unknown.

M3 — Production SRE rule pack (weeks 3–4)

Build: first 10 solid checks then expand toward 15–20 if justified; version applicability; severity/verdict; remediation; unit and integration fixture tests with both safe and unsafe variants; rule docs.
Demo: full production-readiness review of a Java/Spring service and a Python service with representative deployment config.
Gate: expert review of accuracy, no generic unsupported absence warnings; overlap mapping to existing scanners.

M4 — CI and policy controls (week 5)

Build: Git diff-aware scope with repo-context fallback, SARIF, GitHub Actions example, baseline and suppression lifecycle, rule selection/overrides, exit-code policy.
Demo: PR with a newly introduced timeout defect versus pre-existing finding, showing SARIF code-scanning result and opt-in CI failure.
Gate: repeatable CI, no code egress, baseline and expiry behavior verified.

M5 — Independent validation and alpha release (week 6)

Build: held-out independent benchmark; scanner/AI/SRE baseline comparison; false-positive and unknown audit; installation docs, contributor guide, rule-authoring guide, threat model, license/SBOM, release process.
Demo: pipx install (or equivalent) → fifthnine scan . → evidence-backed report in minutes; one CI workflow.
Gate: CEO reviews results and authorizes public alpha only if evidence, accuracy, scope honesty and usability meet agreed targets.

Milestone weeks are planning estimates, not commitments. Work sequentially, demonstrate each gate, and stop for CEO decision if assumptions fail. Do not promise simultaneous completion of all framework/rule combinations.

10. Code organization proposal

fifthnine/
  pyproject.toml
  LICENSE
  README.md
  src/fifthnine/
    cli.py
    core/{workspace.py,config.py,facts.py,findings.py,engine.py,registry.py}
    extractors/{base.py,java.py,python.py,kubernetes.py,docker.py,config_files.py}
    rules/{resilience/,capacity/,deployment/,production_config/,network/,compatibility/}
    output/{console.py,json_report.py,sarif.py}
    policy/{baseline.py,suppressions.py,ci_gate.py}
  tests/{fixtures/,unit/,integration/,e2e/}
  docs/{architecture.md,rule_authoring.md,security.md,coverage.md}
  examples/{java-spring/,python-api/,github-actions/}
  benchmarks/{dev/,public_results/}

This is an illustrative package layout. Modify after repo audit; keep public interfaces stable. No service control plane, multi-tenancy or cloud account required in Phase 1.

11. Security, performance and supply-chain requirements

• Treat repositories as untrusted inputs: protect against symlink escape, oversized files, pathological YAML, zip bombs, secrets in reports, path traversal and parser DoS.
• Avoid executing repository-provided scripts or builds by default. Helm templating, Terraform plan or test execution require explicit safe-mode opt-in/sandboxing later.
• Never scan .git, node_modules, virtualenvs or secrets directories by default; document exclusions and consequences for coverage.
• Use bounded file sizes, parsing timeouts where practical, deterministic rule execution, structured error handling.
• Run without network access; pin dependencies and ship reproducible packages, SBOM and changelog where feasible.
• Publish explicit supported-language/framework and rule coverage matrix.
• Ensure Windows/macOS/Linux compatibility is tested or explicitly constrained; CI Linux is minimum.

12. Definition of done

Phase 1 alpha is ready for CEO review when all are true:

1. fifthnine scan . installs easily and scans at least one real supported codebase without credentials, SaaS or manual dependency graph.
2. At least two documented stacks are meaningfully supported at the claimed coverage level, with nontrivial SRE-design findings.
3. Every blocking finding is version-aware, source-evidenced and reproducible; unknown contexts are not passed as safe.
4. Results are available in readable CLI, JSON and SARIF; baseline and justified suppressions work.
5. GitHub Actions integration is demonstrated in report-only and opt-in gating modes.
6. The benchmark reports both successes and failures versus relevant existing scanners and an LLM, with independently judged findings.
7. Five practitioners can install, run and understand output without CTO intervention (measure actual effort).
8. Docs clearly say FifthNine does not certify production readiness or prevent all outages.
9. Licensing, attribution, privacy and untrusted-input review are completed.
10. CEO expressly authorizes alpha release.

13. What NOT to build in Phase 1

No global Failure Graph; no incident crawler; no always-on observability or incident agent; no enterprise control plane or multi-repo service catalog; no manually curated journey graph; no production cloud credentials or auto-remediation; no proprietary hosted backend; no AI dependency for core scanning; no broad “all languages/OSI layers” claim; no blanket guarantee of production safety.

14. CTO reporting and handoff

For every milestone return: files changed, CLI demo commands and outputs, test counts, known gaps, unsupported/unknown coverage, relevant competitor overlap, benchmark integrity status, and proposed next milestone. Keep docs/fifthnine/PHASE1_IMPLEMENTATION_CONTRACT.md in the repo reflecting CEO-approved decisions. Distinguish plan changes from implementation reality. If conversation context is lost, reread that file. Changes to product mission, rule-blocking semantics or benchmarks require CEO approval.

Your first response should be: (A) repository audit (or explicit inability to access), (B) exact stack/rule scope proposal, (C) risks and tradeoffs, (D) milestone acceptance matrix, (E) any blockers needing CEO decision. Do not silently begin a different product.