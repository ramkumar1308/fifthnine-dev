# Errata (benchmark v1)

## E1: readiness-as-code baseline, checkpoint pa-04 (found 2026-11-09, before any runtime result was seen)

The frozen pattern `^\s{2}port:\s*["']?(?!8080\b)` matches `  port: 8080`: `\s*` can match nothing, and the lookahead then sees
the space before `8080`. pa-04 therefore fails on every case that sets `server.port: 8080`, which is a defect in the CTO's
preregistered configuration, not in readiness-as-code.

Both configurations are reported. `baselines/rac/` is the preregistered one and is scored as run. `baselines/rac-posthoc/` changes
only that pattern, to `^\s{2}port:\s*["']?(?!8080\b)\d`. The change was made after seeing readiness-as-code's output on the cases
and before the runtime ground truth was available, and it is labelled post hoc wherever it is reported. No case was changed.
