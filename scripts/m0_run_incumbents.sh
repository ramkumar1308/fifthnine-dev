#!/usr/bin/env bash
# Re-runs the pinned incumbent scanners on both M0 sample repositories. Offline after install.
# Pinned: kube-linter 0.8.3, hadolint 2.15.1, checkov 3.3.30, ruff 0.17.1, bandit 1.9.4,
#         semgrep 1.182.0 with semgrep/semgrep-rules @ 16b9e6d45f10649f6a531ec4838ed9924372c3bc
# Usage: TOOLS=<dir containing the binaries> RULES=<semgrep rule subset dir> scripts/m0_run_incumbents.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/docs/fifthnine/m0/incumbent-raw"
mkdir -p "$OUT"
for s in orders-service-spring catalog-api-fastapi; do
  d="$ROOT/samples/$s"
  "$TOOLS/kube-linter" lint "$d/k8s" --format plain > "$OUT/kube-linter-default-$s.txt" || true
  "$TOOLS/kube-linter" lint "$d/k8s" --add-all-built-in --format plain > "$OUT/kube-linter-all-$s.txt" || true
  "$TOOLS/hadolint" "$d/Dockerfile" > "$OUT/hadolint-$s.txt" || true
  (cd "$d" && "$TOOLS/checkov" -d . --framework kubernetes,dockerfile,secrets --quiet --skip-download -o json > "$OUT/checkov-$s.json") || true
  (cd "$d" && "$TOOLS/semgrep" scan --metrics=off --disable-version-check --config "$RULES" --json -q . > "$OUT/semgrep-$s.json") || true
done
d="$ROOT/samples/catalog-api-fastapi"
(cd "$d" && "$TOOLS/ruff" check --no-cache --select S,ASYNC,BLE --output-format concise app > "$OUT/ruff-catalog-api-fastapi.txt") || true
(cd "$d" && "$TOOLS/bandit" -q -r app -f txt > "$OUT/bandit-catalog-api-fastapi.txt") || true
