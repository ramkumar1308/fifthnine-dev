"""Render runtime-out/report.json as Markdown (used by the spring-runtime workflow to post the result on the pull request).

    python tests/runtime/spring/report_md.py runtime-out/report.json > comment.md
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


LOGS = Path("runtime-out/logs")


def render(report: dict) -> str:  # type: ignore[type-arg]
    cases = report["cases"]
    totals = report.get("totals", {})
    out = ["### Spring runtime comparison", "",
           f"{len(cases)} cases, {totals.get('checks', 0)} checks, {totals.get('failures', 0)} failures.", ""]
    skipped = {k[len("skipped: "):]: v for k, v in totals.items() if k.startswith("skipped: ")}
    if skipped:
        out += ["Not compared (counted, not claimed as verified):", ""]
        out += [f"- {k}: {v}" for k, v in sorted(skipped.items())]
        out.append("")
    by_kind: Counter[str] = Counter()
    for c in cases:
        for chk in c.get("all_checks", []):
            by_kind[chk["kind"]] += 1
    out += ["Checks by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())), ""]
    out += ["| Case | Boot | Facts | Checks | Failures | Seconds |", "|---|---|---|---|---|---|"]
    for c in cases:
        cells = [c["case"], c.get("boot", ""), c.get("facts", ""), c.get("checks", 0), len(c.get("failures", [])), c.get("seconds", "")]
        out.append("| " + " | ".join(str(x) for x in cells) + " |")
    fails = [(c["case"], f) for c in cases for f in c.get("failures", [])]
    if fails:
        out += ["", "#### Failures", ""]
        for name, f in fails[:60]:
            out.append(f"- `{name}` {f['kind']} `{f['subject']}`: scan says {f['expected']!r}, runtime {f['runtime']!r} {f.get('note', '')}")
    seen: set[str] = set()
    for name, f in fails:
        if f["kind"] in ("start", "build") and len(seen) < 3:
            log = LOGS / f"{name}.log"
            if log.is_file():
                tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-45:])
                out += ["", f"<details><summary>Log tail: {name}</summary>", "", "```", tail[-6000:], "```", "", "</details>"]
                seen.add(name)
    errs = [c for c in cases if c.get("error")]
    for c in errs:
        out.append(f"- `{c['case']}` error: {c['error']}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
