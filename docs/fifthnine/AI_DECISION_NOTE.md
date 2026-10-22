# Decision note: AI-assisted investigation with deterministic verification

Status: CTO recommendation for CEO decision. Nothing here is implemented, and approved M2 scope is unchanged.
Date: 2026-10-22. Author: Claude (CTO).

**Recommendation: EXPERIMENT FIRST.** Run one pre-registered experiment after M2-3 merges, using external agents and FifthNine's fact output, with no AI code in FifthNine. Decide INCLUDE or DEFER on its results. M2 continues unchanged. M3 stays at the approved first-wave rules and does not grow into a large manual rule library before the experiment reports.

## 1. Where AI fits

The contract already allows this shape. Section 7 permits an optional AI mode, after deterministic quality is established, that "may identify candidate patterns or missing context but must cite file/line and cannot mark results verified or block CI." The proposal keeps that boundary and adds one thing: a deterministic verifier that can promote an AI hypothesis to a finding. The verifier, not the model, decides the verdict.

```
discover -> detect -> extract (M2) -> resolve (M2)  ── facts with provenance
                                          │
                     investigate (AI, optional, off by default)
                     reads facts + selected source, emits hypotheses
                                          │
                     verify (deterministic): facts, rule evaluators (M3),
                     evidence checks; derive_verdict() assigns the verdict
                                          │
        findings (verified or qualified)        advisory (unverified, never gates)
```

- **The AI's input is M2's facts, not the raw repository.** Ten of the twelve generalization-cohort corpus repositories exceed one million tokens of source and configuration. The median is about 3.5 million (bytes divided by 4, measured on the pinned commits). No model reads that whole. The resolved facts, with file and line, are a compact map of what a production-readiness review needs; the agent reads source only where a fact or hypothesis points.
- **The AI's output is structured hypotheses, not prose findings.** Each hypothesis states a claim, a subject, a mechanism, and the evidence that would establish it, as fact queries and file:line citations.
- **The schema's `advisory` section** (today fixed at zero items, CEO condition C5) would carry hypotheses that could not be verified. It needs a CEO-approved schema change. Exit codes and CI gating never read it.

## 2. Is AI-first investigation with deterministic verification practical?

Partly, and the experiment has to show which part.

**Practical:**
- **Breadth.** An agent can propose concerns that no rule anticipated, follow call chains (which client call sits on a request path), and judge intent (is this retried POST idempotent).
- **Ranking.** An agent can say which concerns matter for this service.

**Not practical without maintained semantics:** anything whose correct answer depends on precedence or version-specific defaults. Examples:
- which profile is active;
- whether `spring.http.client.read-timeout` binds at all on Boot 3.3;
- whether a probe path exists on the management port;
- what Kubernetes does when a field is unset.

These are exactly the places where a plausible answer is easy to give and often wrong. HX1 was designed to measure that failure. M2-0 is itself evidence: the first version of its answer keys had status errors, and an approved M0 rule note had a version error. Both were caught only by checking against tagged source.

**Two risks:**
- **Volume.** An agent raises many plausible concerns. Without a hard filter, the review becomes noise, which defeats the PR-scan goal of avoiding alert fatigue.
- **Repeatability.** Two runs of the same agent return different hypotheses. Verified findings stay deterministic for a given hypothesis, but the set of hypotheses does not. The experiment measures how much this varies.

## 3. What needs maintained semantics, and what AI can handle

| Needs maintained, cited semantics (deterministic, M2 and M3) | AI can handle flexibly, with verification or as advisory |
|---|---|
| Spring property precedence, profile resolution, relaxed binding, placeholders | Which code paths are request-serving versus background |
| Version-specific defaults (shutdown mode, client timeouts, probe groups) | Whether an operation is idempotent, and so safe to retry |
| Kubernetes object graph, effective container command, Kubernetes defaults | Whether failure handling is adequate (fallbacks, bulkheads, error mapping) |
| Override exposure from out-of-repository sources | Whether observability is adequate (logging on failure paths, metrics, correlation IDs) |
| Arithmetic checks: retry window against deadline, shutdown budget against grace period, rollout against PDB | Spotting unusual patterns no rule covers, and proposing a rule |
| Linkage between workload and source, and when it is unconfirmed | Explaining a verified finding and drafting remediation text |

Several of the CEO's examples belong in the left column and are better as M3 rules on top of M2:
- development endpoints used in production, which needs the effective profile and placeholder resolution M2-2 builds;
- autoscaling that cannot exceed one replica;
- missing or ineffective timeouts;
- exposed administrative endpoints.

The right column is where AI could add something rules cannot.

## 4. How AI hypotheses would be verified

Each hypothesis ends in exactly one of four outcomes:

| Outcome | Condition | Where it appears |
|---|---|---|
| Verified | A rule, or a check template built from M2 facts, evaluates the claim; `derive_verdict()` gives VIOLATION, CONDITIONAL or SATISFIED with the usual assumptions | `findings`, marked as AI-originated |
| Supported | Every cited location exists and says what the hypothesis claims, but no rule can evaluate the mechanism | `advisory`, labelled "needs SRE review", never gating |
| Unknown | The claim depends on a value that is unresolved, out of the repository, or covered by no cited framework semantics | `advisory`, with the missing input named |
| Rejected | A citation does not exist or contradicts the facts, or the claim conflicts with a resolved fact | Dropped from the report, counted in the run log |

The verifier enforces the CEO's rule that "AI must never invent verified findings, framework behaviour or production-effective configurations":
- A claim about framework behaviour must match a cited defaults-table entry, or it cannot be Verified.
- A claim about configuration must match an M2 fact, including its override exposure and link status.
- The AI never assigns severity or verdict.
- Only rules promoted under D5 can block CI, so AI-originated findings never gate in Phase 1.

## 5. How this differs from GitHub Copilot with SRE instructions

Arm A of the experiment is that comparison, and we should expect it to be strong. The differences that matter:

| | Copilot code review with custom instructions | FifthNine hybrid |
|---|---|---|
| Scope | Review comments on a pull request | Whole-repository readiness review; PR mode secondary |
| Configuration semantics | Model reasoning; no resolved effective configuration | M2 resolution with precedence, profiles, defaults and provenance |
| Version handling | Model recall | Cited defaults per version; unsupported versions reported, not guessed |
| Verified versus assumed | Not distinguished | Every finding carries its open assumptions, and unknowns are reported |
| Repeatability and gating | Non-deterministic; not a stable gate | Deterministic rules and fingerprints; AI output never gates |
| Data | Code goes to the hosted service | Offline by default; AI optional with a customer-chosen backend |

The honest challenge: if arm C (agent plus FifthNine facts) does not clearly beat arm A (agent alone) on unsupported claims, version accuracy or evidence quality, the AI layer is Copilot with extra steps, and we should not build it. Our differentiation would then be the deterministic layer alone. That is still the product M1 to M4 are building.

## 6. Smallest experiment that shows incremental value (HX2)

HX2 reuses HX1's machinery (PR #2): five runs per arm, blinded adjudication, scoring frozen before any output is seen, and no single number deciding the outcome. It adds unfamiliar cases and the hybrid arms.

**When:** after M2-3 merges, so `fifthnine scan --facts all` produces Spring and Python facts. The experiment needs no FifthNine AI code. Arm C gets the facts as a JSON file and the CLI as a tool.

**Arms:**
- **A.** The strongest available frontier coding agent, given HX1's expert SRE instructions extended to the CEO's issue list. Not a weak prompt.
- **B.** FifthNine deterministic: executed M2 facts plus the approved rule specifications, labelled "expected, not executed" until M3.
- **C.** Arm A's agent and prompt, plus FifthNine facts and CLI, instructed to cite fact ids.
- **V.** Arm C's output passed through the section 4 verification outcomes. These are applied by a small evaluation script and the adjudicator, with no product code involved. V is the architecture under test.

**Cases:**
- HX1's four blinded cases, plus six unfamiliar cases from the generalization cohort (three Spring, three FastAPI).
- An independent author, not the CTO, creates defect and safe-twin variants of each unfamiliar case.
- Valid findings beyond the planted ones are adjudicated by the outside SRE reviewer.
- The sealed held-out benchmark is not used.
- Runners have no access to answer keys.

**Measures:** the CEO's nine measures (true production-relevant findings, missed issues, false positives and unsupported claims, profile interpretation, version accuracy, evidence quality, repeatability, time and cost, handling of unknowns), reported per arm, per case and per cohort.

**Proposed decision rule, to be frozen with the protocol:**
- **INCLUDE** if V, compared with A:
  - at least halves unsupported claims;
  - matches or beats version accuracy;
  - loses no more than one adjudicated true finding per case;
  - produces at least three valid findings across all cases that B cannot produce.
- **DEFER** if V does not beat A on unsupported claims, or if C and V add nothing over B beyond explanation.
- **Results in between:** reported to the CEO without a recommendation forced by the rule.

## 7. Cost, latency and privacy

**Experiment cost:**
- About four CTO days: harness, cases handed to the author, mapping, report.
- The independent author's time for six case pairs.
- One runner without answer-key access.
- One adjudicator.
- Model usage, recorded per run. HX1 already budgets Copilot at GitHub's published $0.25 to $5 per review; agent costs are recorded from vendor billing, not estimated here.

**Product cost if the experiment says INCLUDE.** This is my estimate, to be refined after HX2:
- three to five engineering weeks after M3, covering the hypothesis schema and the `advisory` schema change, the verifier and check templates, a backend adapter, budget and caching controls, and an evaluation suite;
- maintenance: the verification templates grow with M3 rules, and model drift needs a standing regression run against the corpus.

**Latency:**
- The deterministic scan took between 0.27 s and 2.4 s on the original 20 corpus repositories (M1 baseline).
- An agentic investigation will take minutes per repository; HX2 measures it. It belongs in the full pre-production review, not in default PR scans.

**Privacy:**
- AI stays optional and off by default; the offline scanner never requires it.
- Code goes only to a backend the customer configures: their own hosted model account, or a local model behind an OpenAI-compatible endpoint. FifthNine operates no service.
- Before anything is sent, the existing secret redaction applies.
- The report records which files went to which endpoint.
- CI defaults to no AI.

## 8. Recommendation and scope

**EXPERIMENT FIRST**, with HX2 as defined above, run after M2-3. Until the results are in:
- M2 continues exactly as approved, and arm C depends on it.
- M3 builds only the approved first-wave rules.
- No AI, MCP or agent code enters FifthNine. The Phase 1 lock stands.

Decisions needed:
1. Approve HX2 in principle. I would write the full pre-registered protocol after M2-1 and submit it before M2-3 completes.
2. Name the independent case author and the adjudicator. The outside SRE reviewer (A7) could hold both roles if available.
3. Confirm that a later INCLUDE would come as a formal Phase 1 scope change, covering the `advisory` schema change and a contract amendment to section 7, decided on HX2 evidence.
