"""M2 corpus measurement: run the scanner on every corpus repository, re-check every fact with the semantic oracle,
and compare linkage results with the expectation recorded in corpus.v1.yaml.

Usage: python benchmarks/dev/corpus/m2_report.py CORPUS_DIR OUT_JSON

CORPUS_DIR is where fetch.py placed the repositories. Each scan runs `fifthnine scan --format json --facts all` in a
fresh process, so wall time and peak RSS are per scan. Benchmark tooling only; the offline test suite never runs it.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fifthnine.core.safeparse import load_yaml_documents, to_plain  # noqa: E402
from fifthnine.schema.v1 import ScanResult  # noqa: E402
from tests.support.oracle import OracleReport, run_oracle  # noqa: E402


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024


def main(corpus_dir: Path, out: Path) -> int:
    corpus = to_plain(load_yaml_documents((ROOT / "benchmarks/dev/corpus/corpus.v1.yaml").read_text())[0].data)
    rows = []
    for e in corpus["entries"]:
        repo = corpus_dir / e["repository"].replace("/", "__")
        t0 = time.perf_counter()
        # fresh interpreter per scan so ru_maxrss reflects this scan only
        code = ("import resource, sys, subprocess;"
                "p = subprocess.run([sys.executable, '-m', 'fifthnine', 'scan', sys.argv[1], '--format', 'json', '--facts', 'all'],"
                " capture_output=True, text=True); sys.stdout.write(p.stdout);"
                " sys.stderr.write(str(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))")
        p = subprocess.run([sys.executable, "-c", code, str(repo)], capture_output=True, text=True)  # noqa: S603
        wall = time.perf_counter() - t0
        rss_mb = int(p.stderr.strip().splitlines()[-1]) / 1024 if p.stderr.strip() else None
        result = ScanResult.model_validate_json(p.stdout)
        try:
            oracle = run_oracle(repo, result.facts, result.linkages)
        except Exception as exc:  # noqa: BLE001 - an oracle failure is recorded, never hidden
            oracle = OracleReport(mismatches=[f"oracle error: {type(exc).__name__}: {exc}"])
        links = result.linkages
        observed = "in-repo-app" if any(lk.status.value == "inferred" for lk in links) else ("none" if not links else "not-linked")
        rows.append({
            "id": e["id"], "repository": e["repository"], "cohort": e["cohort"], "framework": e["framework"],
            "files_considered": result.inventory.files_considered, "facts": len(result.facts),
            "facts_unresolved": sum(f.status.value == "unresolved" for f in result.facts),
            "facts_default": sum(f.status.value == "default" for f in result.facts),
            "wall_s": round(wall, 2), "max_rss_mb": round(rss_mb, 1) if rss_mb else None,
            "oracle_checked": oracle.checked, "oracle_mismatches": oracle.mismatches, "oracle_skipped": oracle.skipped,
            "links_inferred": sum(lk.status.value == "inferred" for lk in links),
            "links_unresolved": sum(lk.status.value == "unresolved" for lk in links),
            "link_reasons": sorted({ev[len("reason: "):] for lk in links for ev in lk.evidence if ev.startswith("reason: ")})[:6],
            "linkage_expectation": e["linkage_expectation"], "linkage_observed": observed,
            "status": result.status.value,
        })
        print(f"{e['id']} {e['repository']}: {wall:.2f}s rss={rows[-1]['max_rss_mb']}MB facts={len(result.facts)} "
              f"oracle {oracle.checked} checked / {len(oracle.mismatches)} mismatches; links {rows[-1]['links_inferred']} inferred "
              f"{rows[-1]['links_unresolved']} unresolved (expected {e['linkage_expectation']})", flush=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
