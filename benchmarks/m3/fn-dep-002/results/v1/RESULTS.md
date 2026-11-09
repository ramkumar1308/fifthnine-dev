# FN-DEP-002 benchmark v1: results (2026-11-09)

Cases frozen at commit fc2fde6 (`MANIFEST.sha256`). Runtime ground truth from workflow run 42569536246 on commit 5edb45e, stored on
branch `benchmark/fn-dep-002-runtime-results`. Scoring: `harness/score.py`, output `scored.json`. Raw tool outputs are under `raw/`.

FifthNine FN-DEP-002 has not been run. The rule is not implemented: it waits for the M3 implementation gate. Nothing below is an
FN-DEP-002 result.

## Ground truth

All 36 repositories built and ran in kind (Kubernetes server v1.32.2, kind v0.27.0, Temurin 21.0.13, Maven 3.9.15). 31 cases are assessable
(16 ALIGNED, 15 MISALIGNED). The 5 NOT_DETERMINABLE cases each flipped between ALIGNED and MISALIGNED under their two external inputs,
as the author specified. The author's expected class matched the runtime class in 36 of 36 cases, so no case needed an erratum.

## Tools

| Tool | Accuracy on 31 assessable (95% CI) | Defects detected (of 15) | False positives (of 16 aligned) | Confident false-safe | Correct unknowns (of 5) |
|---|---|---|---|---|---|
| AI coding agent (fresh agent per 6 cases, frozen prompt) | 31 (0.89 to 1.00) | 15 | 0 | 0 | 5 |
| readiness-as-code, preregistered checkpoints | 15 (0.32 to 0.65) | 15 | 16 | 0 | 0 |
| readiness-as-code, post hoc pa-04 fix (ERRATA E1) | 16 (0.35 to 0.68) | 13 | 13 | 2 (pb-16, pb-24) | 3 |
| FifthNine FN-DEP-002 | not run (M3 gate) | | | | |

The agent also gave the exact port, path and HTTP status of 75 of 75 probes. It is the same model family as the case author, which
shares knowledge of Spring semantics with it; the runtime run is what makes its answers checkable, not the author.

readiness-as-code checks literals with line-based regular expressions. Its false positives come from valid non-default
configurations (a separate management port with probes on that port, a custom base path that the probes follow); its two
confident false-safe answers are a liveness group disabled by `spring.main.cloud-platform: none` (pb-16) and health made
inaccessible by `enabled-by-default: false` (pb-24), neither of which a text check sees.

## FifthNine current build (facts only, commit 97b4b21, PR #8 branch)

`fifthnine scan --format json --facts all`: 36 of 36 scans INCOMPLETE with 0 findings, 0.38 to 0.50 s each (16.5 s in total including
interpreter start-up). In 36 of 36 cases the container-to-source link is unresolved: the image name `fifthnine-bench/<case>:1` does
not match the Maven artifact `app`, and M2-1's linkage rules (D3) do not guess. Without the link, the scanner cannot apply the
container's environment, arguments or Kubernetes detection, so the health endpoints are unresolved in 30 of 36 cases and every
Spring value carries `env_possible`. The image naming came from the harness contract the CTO gave the author.

Declared links (`.fifthnine.yml`, `fifthnine links accept`) are M2-5 work. The FN-DEP-002 run is therefore preregistered in two
modes (see PROTOCOL.md, addendum A1).

## Timing and effort

| Tool | Per case | Setup |
|---|---|---|
| AI coding agent | 61 to 84 s per batch of six (about 10 to 14 s a case), 83k to 93k tokens a batch | prompt only |
| readiness-as-code | 0.10 s (3.7 s for all 36) | 10 checkpoints written before the cases existed |
| FifthNine current build | 0.46 s | none |
| Runtime ground truth | 7 to 36 s build and image load, then a window of at least 150 s | kind cluster in CI |

## Incidents

- One agent (cases pb-25 to pb-30) ran a hash command over `App.java` in the other case directories, against its access rule. The
  files are identical boilerplate, and it reported seeing hashes only.
- readiness-as-code's preregistered pa-04 pattern had a regex defect (ERRATA E1). Both configurations are reported.
