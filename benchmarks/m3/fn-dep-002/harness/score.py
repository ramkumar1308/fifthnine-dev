"""Score each tool's predictions against the runtime ground truth (PROTOCOL.md, Metrics).

Usage: python score.py --cases DIR --runtime DIR --rac DIR --rac-posthoc DIR --agent DIR --out results.json
"""

from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path

import yaml


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    z = 1.959964
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def truth(cases: Path, runtime: Path) -> tuple[dict[str, str], dict[str, dict]]:
    runs: list[dict] = []
    for f in sorted(glob.glob(str(runtime / "*" / "runtime-shard-*.json"))):
        runs += json.loads(Path(f).read_text())["runs"]
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["case"], []).append(r)
    gt: dict[str, str] = {}
    detail: dict[str, dict] = {}
    for d in sorted(p for p in cases.iterdir() if (p / "case.yaml").exists()):
        meta = yaml.safe_load((d / "case.yaml").read_text())
        rs = by.get(d.name, [])
        classes = sorted({r["runtime_class"] for r in rs})
        if meta["expected"] == "NOT_DETERMINABLE":
            flips = classes == ["ALIGNED", "MISALIGNED"]
            gt[d.name] = "NOT_DETERMINABLE" if flips else (classes[0] if len(classes) == 1 else "UNDECIDED")
        else:
            gt[d.name] = classes[0] if len(classes) == 1 else "UNDECIDED"
        detail[d.name] = {"author": meta["expected"], "runtime": classes, "boot": str(meta["boot_version"]), "dimensions": meta.get("dimensions", [])}
    return gt, detail


def rac(d: Path) -> dict[str, str]:
    out = {}
    for f in sorted(d.glob("*.json")):
        res = json.loads(f.read_text())["results"]
        red = [r for r in res if r["status"] == "fail" and r["severity"] == "red"]
        other = [r for r in res if (r["status"] == "fail" and r["severity"] != "red") or r["status"] in ("warn", "skip", "needs_review")]
        out[f.stem] = "MISALIGNED" if red else ("UNKNOWN" if other else "ALIGNED")
    return out


def agent(d: Path) -> dict[str, str]:
    out = {}
    for f in sorted(d.glob("batch-*.json")):
        for item in json.loads(f.read_text()):
            out[item["repo"]] = {"NOT_DETERMINABLE": "UNKNOWN"}.get(item["verdict"], item["verdict"])
    return out


def metrics(pred: dict[str, str], gt: dict[str, str]) -> dict:
    assess = [c for c, g in gt.items() if g in ("ALIGNED", "MISALIGNED")]
    nd = [c for c, g in gt.items() if g == "NOT_DETERMINABLE"]
    mis = [c for c in assess if gt[c] == "MISALIGNED"]
    ali = [c for c in assess if gt[c] == "ALIGNED"]
    correct = [c for c in assess if pred.get(c) == gt[c]]
    tp = [c for c in mis if pred.get(c) == "MISALIGNED"]
    fn_safe = [c for c in mis if pred.get(c) == "ALIGNED"]
    fn_unknown = [c for c in mis if pred.get(c) == "UNKNOWN"]
    fp = [c for c in ali if pred.get(c) == "MISALIGNED"]
    abst = [c for c in ali if pred.get(c) == "UNKNOWN"]
    nd_ok = [c for c in nd if pred.get(c) == "UNKNOWN"]
    nd_safe = [c for c in nd if pred.get(c) == "ALIGNED"]
    nd_mis = [c for c in nd if pred.get(c) == "MISALIGNED"]
    return {
        "assessable": len(assess), "accuracy": f"{len(correct)}/{len(assess)}", "accuracy_ci95": wilson(len(correct), len(assess)),
        "defects": len(mis), "detected": len(tp), "detection_ci95": wilson(len(tp), len(mis)),
        "missed_confident_aligned": fn_safe, "missed_unknown": fn_unknown,
        "aligned": len(ali), "false_positives": fp, "fp_ci95": wilson(len(fp), len(ali)), "unknown_on_aligned": abst,
        "not_determinable": len(nd), "correct_unknowns": nd_ok, "nd_called_aligned": nd_safe, "nd_called_misaligned": nd_mis,
        "confident_false_safe": fn_safe + nd_safe,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--cases", "--runtime", "--rac", "--rac-posthoc", "--agent", "--out"):
        ap.add_argument(a, required=True)
    a = ap.parse_args()
    gt, detail = truth(Path(a.cases), Path(a.runtime))
    tools = {"readiness-as-code (preregistered)": rac(Path(a.rac)), "readiness-as-code (post hoc pa-04 fix)": rac(Path(a.rac_posthoc)),
             "AI coding agent": agent(Path(a.agent))}
    author = {c: {"NOT_DETERMINABLE": "UNKNOWN"}.get(d["author"], d["author"]) for c, d in detail.items()}
    out = {"ground_truth": gt, "author_vs_runtime": {c: [d["author"], d["runtime"]] for c, d in detail.items()
                                                     if not (d["author"] == "NOT_DETERMINABLE" and gt[c] == "NOT_DETERMINABLE") and [d["author"]] != d["runtime"]},
           "tools": {name: {"metrics": metrics(p, gt), "predictions": p} for name, p in tools.items()},
           "author_expectation_as_a_tool": metrics(author, gt), "cases": detail}
    Path(a.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    for name, t in out["tools"].items():
        m = t["metrics"]
        print(f"{name}: accuracy {m['accuracy']} {m['accuracy_ci95']}, detected {m['detected']}/{m['defects']}, FP {len(m['false_positives'])}/{m['aligned']}, "
              f"false-safe {m['confident_false_safe']}, ND unknown {len(m['correct_unknowns'])}/{m['not_determinable']}, unknown on assessable "
              f"{len(m['missed_unknown']) + len(m['unknown_on_aligned'])}")
    print("author vs runtime disagreements:", out["author_vs_runtime"])


if __name__ == "__main__":
    main()
