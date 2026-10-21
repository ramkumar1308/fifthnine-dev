# FifthNine Scan CLI interface, v1 (frozen at M0)

Frozen means: commands, flags, defaults and exit codes below do not change without CEO approval. New optional flags can be added. Everything is offline; no command opens a network connection or executes repository code.

## The default path

```
fifthnine scan .
```

No config file, no flags, no account. Output: console report, report-only mode, exit 0 unless the scan itself fails. Inferred service linkages are shown with the one command that confirms them.

## Commands

### `fifthnine --version`
Prints `fifthnine <version> (schema fifthnine.result/v1, rules <pack>@<version>)`.

### `fifthnine scan [PATH]`
PATH defaults to `.`.

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME[,NAME]` | from `.fifthnine.yml`, then from in-repo signals | Spring profiles to treat as active. Recorded in `scan.profile_source` |
| `--base-ref REF` | none | Diff mode: evaluate HEAD and REF (read with `git show`, never checked out), report findings as new, existing or fixed |
| `--format console\|json\|sarif` | `console` | Output format |
| `--output FILE` | stdout | Write the formatted report to FILE. A console summary still prints to stderr unless `--quiet` |
| `--baseline FILE` | `.fifthnine-baseline.json` if present | Mark matching findings `existing` |
| `--config FILE` | `.fifthnine.yml` if present | Repository config |
| `--mode report\|gate` | `report` (or config `policy.mode`) | `gate` exits 1 on blocking findings |
| `--fail-on low\|medium\|high\|critical` | `high` | Gate threshold |
| `--rule ID` / `--skip-rule ID` | all applicable | Repeatable |
| `--facts referenced\|all\|none` | `referenced` | Which facts the JSON carries |
| `--max-file-size BYTES` | 1048576 | Larger files are skipped and listed |
| `--timing` | off | Adds durations. Off by default so repeated scans produce byte-identical JSON |
| `--no-color`, `--quiet`, `--verbose` | | |

### `fifthnine links [PATH]`
Shows proposed service linkages (workload container, Dockerfile, source root) with the evidence for each, and their status.

### `fifthnine links accept [PATH] [--yes]`
Turns inferred linkages into declared ones in `.fifthnine.yml` (E4 confirmation path, D3 safeguards):

- Prints the exact YAML it will write, with the evidence for each link, and waits for confirmation. Without a TTY it refuses (exit 2) unless `--yes` is passed. `fifthnine scan` never accepts links itself.
- Each entry records `accepted_by: links-accept`, `accepted_on` and an `evidence_digest` over the Dockerfile, the workload container's image repository, command, args and ports, and the build descriptor identity.
- Refuses ambiguous proposals and names the entry that needs a manual choice.
- On later scans, a changed digest marks the link `stale`; stale links count as inferred until accepted again.

Declarations that are not linkages (`external_overrides`, `platform.admission_defaults`) are never written by this command. A team adds them by hand, and findings that rely on them cite the line.

### `fifthnine rules list [--domain D] [--format table|json]`
Lists rule ID, version, domain, blocking capability, supported stacks.

### `fifthnine rules explain RULE_ID`
Prints semantics, applicability by version, verdict logic, known false-positive traps, references and the overlap note with incumbent scanners.

### `fifthnine baseline create [--path PATH] [--output FILE] [--profile ...]`
Runs a scan and writes current VIOLATION, CONDITIONAL and UNKNOWN fingerprints to FILE (default `.fifthnine-baseline.json`). No timestamps, so the file is stable in version control.

### `fifthnine schema result|config|baseline`
Prints the frozen JSON Schema for integrators.

## Exit codes

| Code | When |
|---|---|
| 0 | Scan completed and no finding is policy-blocking. Always the case in `report` mode |
| 1 | `gate` mode and at least one finding is blocking-eligible with severity at or above `--fail-on` |
| 2 | Execution or configuration error: unreadable path, invalid `.fifthnine.yml`, unknown rule ID, bad base ref |

An expired suppression never causes exit 2. It is reported as `suppression_expired` and the finding is re-activated.

## Console layout (abridged from `simulated/orders-service-spring.default.console.txt`)

```
FifthNine Scan 0.1.0   .   report mode   profile: prod (k8s/deployment.yaml:33)
Stack: spring-boot 3.4.13 [tested], spring-framework 6.2.15 [tested], java 21 [tested], ...
Status: FAIL_VERIFIED   2 violation  6 conditional  0 unknown  3 satisfied   would block in gate mode (>= high): 0

VIOLATION   high    FN-RES-001   PaymentClient can wait forever on the payment service
...
CONDITIONAL medium  FN-DEP-003   Shutdown budget 55s exceeds pod grace period 30s
                                computed: 10s preStop + 45s timeout-per-shutdown-phase = 55s > 30s ...
                                from repository evidence: VIOLATION if the assumptions below hold
                                open assumption: out-of-repo environment sources do not override these values (...)
                                open assumption: linkage L1 is correct (run `fifthnine links accept`)
...
FifthNine reports evidence about specific, documented reliability checks ... It does not certify production readiness ...
```

The `[tested]` labels are a projection; today the same scan shows `[untested]` and 0 violations (`simulated/orders-service-spring.today-untested.console.txt`).

## CI usage

Report-only (default) in any CI:

```
pipx install fifthnine
fifthnine scan . --format sarif --output fifthnine.sarif
fifthnine scan . --format json --output fifthnine.json
```

Opt-in gate after a pilot period:

```
fifthnine scan . --mode gate --fail-on high --baseline .fifthnine-baseline.json
```

SARIF mapping: VIOLATION -> `error` (high, critical) or `warning`; CONDITIONAL -> `note`; UNKNOWN and coverage notices -> `invocations[0].toolExecutionNotifications`, so code scanning UIs show only findings with evidence.

## Running next to other scanners

FifthNine does not replace security or IaC scanners and does not repeat their checks. Recommended pairing, documented in `docs/coverage.md` at M4:

| Concern | Tool |
|---|---|
| Kubernetes security posture, missing requests/limits, probe presence | Checkov, kube-linter |
| Dockerfile hygiene, shell-form CMD | Hadolint |
| Python timeouts on `requests`/`httpx` | Ruff S113 or Bandit B113 |
| Secrets in files | gitleaks, Trivy |
| SQL migrations | Squawk, Atlas |
| Reliability behaviour across code, config and manifests | FifthNine |
