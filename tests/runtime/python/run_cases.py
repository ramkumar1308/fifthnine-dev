"""M2-3 runtime comparison: scan each case offline, then ask the real libraries (FastAPI, pydantic-settings, tenacity, httpx,
gunicorn --print-config, uvicorn's click parser) what they resolve for the same repository and environment, and compare.

Usage: python tests/runtime/python/run_cases.py --python PYTHON_WITH_LIBRARIES --out DIR
The scanner runs in this interpreter, offline. PYTHON_WITH_LIBRARIES has the pinned libraries installed and runs probe.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fifthnine.core.engine import ScanOptions, run_scan  # noqa: E402
from fifthnine.schema.v1 import Fact, FactStatus  # noqa: E402
from tests.runtime.python.cases import CASES, Case, write_case  # noqa: E402

PROBE = Path(__file__).with_name("probe.py")
RESOLVED = (FactStatus.DECLARED, FactStatus.DEFAULT, FactStatus.INFERRED)


def _env(facts: list[Fact], case: Case) -> dict[str, str]:
    env = dict(case.docker_env)  # image ENV, then the container env over it
    env.update({k: v for k, v in (case.env or {}).items() if v is not None})
    env.update(case.run_env)
    return env


FAKE = """#!{python}
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if name.startswith("python") and args[:1] == ["-m"]:
    name, args = args[1], args[2:]
with open(os.environ["FN_ARGV_OUT"], "a") as f:
    f.write(json.dumps([name, *args]) + "\\n")
"""


def _command(facts: list[Fact]) -> list[str] | None:
    """The container's argv as the scan's facts describe it: the effective command, or the image ENTRYPOINT and CMD when the
    effective command is unresolved only because the base image ENTRYPOINT is unknown."""
    f = next((f for f in facts if f.kind == "k8s.container.effective_command"), None)
    if f is not None and f.value:
        return list(f.value)  # type: ignore[arg-type]
    def argv_of(kind: str) -> list[str]:
        v = next((x.value for x in facts if x.kind == kind and x.value), None)
        if not isinstance(v, dict):
            return []
        return list(v["argv"])  # the Dockerfile model already writes shell form as SHELL + the string

    argv = [*argv_of("docker.entrypoint"), *argv_of("docker.cmd")]
    return [str(a) for a in argv] or None


def _launched(argv: list[str], env: dict[str, str], cwd: Path) -> list[str]:
    """Run argv with fake gunicorn, uvicorn and python on PATH (so a real shell expands it) and return what the server would get."""
    with tempfile.TemporaryDirectory() as tmp:
        b = Path(tmp)
        for n in ("gunicorn", "uvicorn", "python", "python3"):
            (b / n).write_text(FAKE.replace("{python}", sys.executable))
            (b / n).chmod(0o755)
        out = b / "argv.jsonl"
        run_argv = [w.rsplit("/", 1)[-1] if w.rsplit("/", 1)[-1] in ("gunicorn", "uvicorn", "python", "python3") else w for w in argv]
        subprocess.run(run_argv, cwd=cwd, env={"PATH": f"{b}:/usr/bin:/bin", "FN_ARGV_OUT": str(out), **env}, capture_output=True, timeout=60,
                       check=False, stdin=subprocess.DEVNULL)
        if not out.exists():
            return []
        return json.loads(out.read_text().splitlines()[0])


def _after(argv: list[str], name: str) -> list[str]:
    for i, w in enumerate(argv):
        if w.rsplit("/", 1)[-1] == name:
            return argv[i + 1:]
    return []


def spec_for(facts: list[Fact], env: dict[str, str] | None = None, cwd: Path | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    for f in facts:
        if f.status not in RESOLVED:
            continue
        if f.kind == "python.asgi_app":
            v = f.value
            wanted = [[r.value["method"], r.value["path"]] for r in facts if r.kind == "fastapi.route" and r.status in RESOLVED]  # type: ignore[index]
            spec["app"] = ["routes", v["module"], v["attribute"], wanted]  # type: ignore[index]
        elif f.kind == "python.settings_field":
            dotted, field = f.subject.key[len("python:"):].split("#")
            spec[f"settings:{dotted}#{field}"] = ["settings", dotted, field, f.value["env"]]  # type: ignore[index]
        elif f.kind in ("python.retry.stop", "python.retry.wait"):
            spec[f"retry:{f.subject.key[len('python:'):]}"] = ["retry", f.subject.key[len("python:"):]]
        elif f.kind == "python.http_client.timeout":
            spec[f"client:{f.subject.key[len('python:'):]}"] = ["client", f.subject.key[len("python:"):]]
    cmd = _command(facts)
    launched = _launched(cmd, env or {}, cwd or Path(".")) if cmd else []
    if launched[:1] == ["gunicorn"] and any(f.kind == "gunicorn.setting" for f in facts):
        spec["gunicorn"] = ["gunicorn", launched[1:]]
    if launched[:1] == ["uvicorn"] and any(f.kind == "uvicorn.setting" for f in facts):
        spec["uvicorn"] = ["uvicorn", launched[1:]]
    return spec


def _norm_bind(v: Any) -> Any:
    if isinstance(v, str) and v.startswith("["):
        return [x.strip().strip("'\"") for x in v.strip("[]").split(",") if x.strip()]
    return v


def compare(facts: list[Fact], rt: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    checks: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def check(kind: str, subject: str, ok: bool, want: Any, got: Any) -> None:
        checks.append({"kind": kind, "subject": subject, "ok": ok, "scan": want, "runtime": got})

    for f in facts:
        if not f.kind.startswith(("python.", "fastapi.", "gunicorn.", "uvicorn.")):
            continue
        if f.status not in RESOLVED:
            skipped[f"{f.kind} {f.status.value}"] = skipped.get(f"{f.kind} {f.status.value}", 0) + 1
            continue
        key = f.subject.key
        if f.kind == "fastapi.route":
            want = f"{f.value['method']}:{f.value['path']}"  # type: ignore[index]
            got = rt.get("app", {}).get("status", {}).get(want)
            check(f.kind, key, isinstance(got, int) and got not in (404, 405), want, got)
        elif f.kind == "fastapi.route_table" and f.value["complete"]:  # type: ignore[index]
            ops = rt.get("app", {}).get("openapi", [])
            check(f.kind, key, f.value["routes"] == len(ops), f.value["routes"], ops)  # type: ignore[index]
        elif f.kind == "fastapi.debug":
            got = rt.get("app", {}).get("debug")
            check(f.kind, key, got == f.value, f.value, got)
        elif f.kind == "python.settings_field":
            dotted, field = key[len("python:"):].split("#")
            got = rt.get(f"settings:{dotted}#{field}", {})
            want = f.value
            ok = got.get("required") == bool(want.get("required")) and (want.get("required") or got.get("default") == want["default"])  # type: ignore[union-attr]
            check(f.kind, key, bool(ok), want, got)
            if "env_binds" in got:
                check(f.kind + ":env", key, got["env_binds"] is True, want["env"], got["env_binds"])  # type: ignore[index]
        elif f.kind in ("python.retry.stop", "python.retry.wait"):
            got = rt.get(f"retry:{key[len('python:'):]}", {}).get(f.kind.rsplit(".", 1)[-1])
            want = f.value
            ok = isinstance(got, dict) and _sub(want, got)
            check(f.kind, key, ok, want, got)
        elif f.kind == "python.http_client.timeout":
            got = rt.get(f"client:{key[len('python:'):]}", {})
            check(f.kind, key, got == f.value, f.value, got)
        elif f.kind == "gunicorn.setting":
            name = key.rsplit("#", 1)[1]
            raw = rt.get("gunicorn", {}).get(name)
            want = f.value
            got: Any = _norm_bind(raw) if name == "bind" else raw
            if name == "bind":
                ok = got == ([want] if isinstance(want, str) else want)
            else:
                ok = str(got) == str(want)
            check(f.kind, key, ok, want, raw)
        elif f.kind == "uvicorn.setting":
            name = key.rsplit("#", 1)[1]
            got = rt.get("uvicorn", {}).get(name)
            check(f.kind, key, str(got) == str(f.value), f.value, got)
        else:
            skipped[f"{f.kind} (not observable at runtime)"] = skipped.get(f"{f.kind} (not observable at runtime)", 0) + 1
    return checks, skipped


def _sub(want: Any, got: Any) -> bool:
    """Every field the scan reports equals the runtime value (the runtime also reports defaults the scan does not list)."""
    if isinstance(want, dict) and isinstance(got, dict):
        return all(k in got and _sub(v, got[k]) for k, v in want.items())
    if isinstance(want, list) and isinstance(got, list):
        return len(want) == len(got) and all(_sub(a, b) for a, b in zip(want, got, strict=True))
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return float(want) == float(got)
    return bool(want == got)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"cases": []}
    work = Path(tempfile.mkdtemp(prefix="fn-pyrt-"))
    total = failures = 0
    for case in CASES:
        d = work / case.name
        if case.sample:
            shutil.copytree(ROOT / "samples" / case.sample, d)
        else:
            d.mkdir()
            write_case(d, case)
        t0 = time.time()
        res = run_scan(ScanOptions(path=d, facts="all"))
        scan_s = time.time() - t0
        facts = list(res.facts)
        spec = spec_for(facts, _env(facts, case), d)
        p = subprocess.run([a.python, str(PROBE), json.dumps(spec)], cwd=d, capture_output=True, text=True, timeout=300,
                           env={"PATH": "/usr/bin:/bin", **_env(facts, case)})
        try:
            rt = json.loads(p.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            rt = {"error": p.stderr[-1000:]}
        checks, skipped = compare(facts, rt)
        fails = [c for c in checks if not c["ok"]]
        total += len(checks)
        failures += len(fails)
        report["cases"].append({"case": case.name, "checks": len(checks), "failures": fails, "not_compared": skipped,
                                "scan_s": round(scan_s, 3), "probe_errors": {k: v for k, v in rt.items() if isinstance(v, dict) and "error" in v}})
        print(f"{case.name}: {len(checks)} checks, {len(fails)} failures, not compared {sum(skipped.values())}")
        for c in fails[:6]:
            print("   FAIL", c["kind"], c["subject"], "scan", c["scan"], "runtime", str(c["runtime"])[:200])
    report["totals"] = {"cases": len(CASES), "checks": total, "failures": failures}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"TOTAL {len(CASES)} cases, {total} checks, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
