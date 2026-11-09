# FN-DEP-002 benchmark protocol (preregistered)

Version 1. Written 2026-11-09, before any case existed. This file, `baselines/rac/checkpoint-definitions.json` and `baselines/agent/PROMPT.md` are committed first. The cases are committed in a second commit with a SHA-256 manifest. Nothing in either commit changes after any tool output on the cases has been seen. A defect found later in a case is recorded in `ERRATA.md` and fixed only in a new benchmark version; version 1 results are reported as run.

## Question

For each case: in the production deployment the repository describes, does every Kubernetes `httpGet` probe (readiness, liveness, startup) reach a Spring Boot Actuator health endpoint that answers HTTP 200 when the application is healthy?

Classes: `ALIGNED`, `MISALIGNED`, `NOT_DETERMINABLE` (the answer depends on input outside the repository).

## Cases

36 cases, written by a separate agent instance that was told not to read the FifthNine source, the FifthNine docs (including `docs/fifthnine/m0/RULES.md`), or the readiness-as-code material, and to derive each expected outcome from upstream Spring Boot sources (3.2.12, 3.3.13, 3.4.13, 3.5.16) and Kubernetes documentation. It is the same model family as the CTO, so it is independent of the CTO's reasoning and of the FifthNine implementation, not of the model. Required spread: each Boot version in at least 6 cases; roughly half `ALIGNED`, 40 percent `MISALIGNED`, 4 to 6 `NOT_DETERMINABLE`; management port, base paths, path mapping, context path, probe groups, additional paths, exposure, `access` versus `enabled`, named probe ports, profile files, Deployment env overrides, in-repo ConfigMap env overrides, placeholders, command-line args, startup probe interactions.

Each case has `repo/` (the only input any tool sees), `case.yaml` (expected outcome, per-probe expectation, rationale, citations; hidden from tools) and, for `NOT_DETERMINABLE` cases, `runtime/` (the out-of-repository manifests applied only in the runtime run).

## Ground truth

1. **Runtime** (primary). The `fn-dep-002-runtime` workflow builds each repository with Maven, builds the image from the repository's Dockerfile, loads it into a kind cluster and applies `k8s/` (plus `runtime/` where present) in a fresh namespace. Observed per case: pod `Ready`, container restart count, `started`, kubelet `Unhealthy` events by probe, and the HTTP status of each probe's port and path fetched through `kubectl port-forward`. Runtime class: `ALIGNED` when the pod is Ready at the end of the window, has not restarted, and every probe URL answers 200; otherwise `MISALIGNED`. The window is at least 150 s and long enough for every probe's `initialDelaySeconds + periodSeconds x failureThreshold` plus 30 s. Kind, Kubernetes, JDK and Maven versions are recorded with the results.
2. **Author expectation** is used only where the runtime run could not decide a case (build failure, infrastructure failure), and such cases are listed. Where runtime and author disagree, runtime wins and the disagreement is reported; the case is not edited.
3. For `NOT_DETERMINABLE` cases the reference answer is `NOT_DETERMINABLE`. The runtime run of each supplied external input shows that the outcome does change with it; if it does not change, the case is reported as mislabelled (in `ERRATA.md`) and scored against the runtime outcome.

## Tools and how their output maps to the classes

| Tool | Configuration (frozen) | MISALIGNED | ALIGNED | UNKNOWN |
|---|---|---|---|---|
| readiness-as-code 0.7.1 | `baselines/rac/checkpoint-definitions.json`: 10 generic checkpoints written before the cases existed, no per-case pins, no attestations, no exceptions | any red checkpoint fails | no red or yellow failure | a yellow failure, `warn`, `skip` or `needs_review`, and no red failure |
| AI coding agent | `baselines/agent/PROMPT.md`, one fresh agent per six cases | `MISALIGNED` | `ALIGNED` | `NOT_DETERMINABLE` |
| FifthNine FN-DEP-002 | runs only after the CEO opens the M3 implementation gate; rule code is written against `docs/fifthnine/m0/RULES.md` and the development fixtures, never against these cases | `VIOLATION` on any probe | `SATISFIED` on every httpGet probe | `CONDITIONAL`, `UNKNOWN`, or no verdict |
| FifthNine current build (facts only) | `fifthnine scan --format json --facts all` | not applicable: there is no rule | not applicable | not applicable |

The FN-DEP-002 specification is the one in `docs/fifthnine/m0/RULES.md` at the commit of this protocol. The CTO commits the cases and runs the harness, so the rule author will have seen the case repositories and `case.yaml`; this is a known limit of the setup. Any change to the FN-DEP-002 specification or to the M2 resolver after this commit is listed in the results with its reason and the commit, so a reader can see whether a change was made after the cases were visible.

The FifthNine current-build run is reported as a fact-accuracy measurement only: for each probe, whether the `spring.actuator.endpoint` facts (port and path) for the probed group equal what the running application served. It is not reported as a detection result, and no "expected M3" verdict is presented as executed.

## Metrics

Per tool, against ground truth:

- **Assessable cases**: ground truth `ALIGNED` or `MISALIGNED`.
- **Accuracy on assessable cases** = definite correct answers / assessable cases. An UNKNOWN on an assessable case is an abstention: not correct, not false-safe, counted separately.
- **Defects detected** (true positives), **defects missed** (ALIGNED or UNKNOWN on a MISALIGNED case, listed separately), **false positives** (MISALIGNED on an ALIGNED case).
- **Confident false-safe**: `ALIGNED` on a `MISALIGNED` case, or `ALIGNED` on a `NOT_DETERMINABLE` case. Target 0.
- **Correct unknowns**: UNKNOWN on a `NOT_DETERMINABLE` case. **Unsupported**: cases a tool reports as outside its coverage, with the reason it gives.
- **Evidence provenance**: for each MISALIGNED verdict, whether the tool cites the file and line of the probe and of each configuration input that decides it (checked by hand against the repository).
- **Execution time** per case and in total; setup and configuration effort.
- 95 percent Wilson intervals on accuracy, detection rate and false-positive rate. With 36 cases the intervals are wide; results describe this set, not a population.

## Acceptance targets (CEO, 2026-11-09)

For FifthNine FN-DEP-002: accuracy at least 95 percent on assessable cases, zero confident false-safe conclusions, and an explicit UNKNOWN for every case outside verified coverage. The evaluator must distinguish a misaligned probe from a configuration outside coverage. The targets are not changed after results are seen.

## Order of work

1. Commit this protocol and the two baseline configurations.
2. Receive the cases; commit them unchanged with `MANIFEST.sha256`.
3. Runtime ground truth run.
4. readiness-as-code, AI agent and FifthNine current-build runs (any order; none sees ground truth).
5. FifthNine FN-DEP-002 run, after the M3 gate and after the rule passes its development fixtures.
6. Report all results, including failures.

## Addendum A1 (2026-11-09, before FN-DEP-002 exists)

Written after the FifthNine current-build facts run showed that none of the 36 container-to-source links is proposed (image names
`fifthnine-bench/<case>:1` do not match the build identity `app`), and before any FN-DEP-002 code exists. FN-DEP-002 is run twice:

1. **Default mode**: `fifthnine scan` with no `.fifthnine.yml`. An unresolved link gives UNKNOWN or CONDITIONAL; scored as UNKNOWN.
2. **Declared-link mode**: each case gets a `.fifthnine.yml` that declares its one Deployment container's link to source root `.`,
   written by `fifthnine links accept` once M2-5 provides it (or by hand to the same schema). This is the onboarding step a team
   would take, comparable to writing readiness-as-code checkpoints. The file is the same for every case apart from the case id and
   is generated by a script committed before the run.

Both modes are reported. The acceptance targets apply to declared-link mode; default mode is reported for what a first scan shows.
