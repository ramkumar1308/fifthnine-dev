"""Runtime ground truth for the FN-DEP-002 benchmark.

For each case: build the jar with Maven, build the image from the case's own Dockerfile, load it into kind, apply the
case's k8s/ manifests (plus one runtime/ input for NOT_DETERMINABLE cases) in a fresh namespace, observe the pod for a
fixed window, then fetch every httpGet probe URL through kubectl port-forward. The harness reads case.yaml only to
learn which runtime inputs exist; it never reads the expected outcome.

Usage: python run_runtime.py --cases DIR --out DIR [--shard I/N] [--parallel K]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

MIN_WINDOW = 150
SLACK = 30


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {p.stderr[-2000:]}{p.stdout[-2000:]}")
    return p


def load_docs(paths: list[Path]) -> list[dict]:
    docs = []
    for p in paths:
        for d in yaml.safe_load_all(p.read_text()):
            if isinstance(d, dict):
                docs.append(d)
    return docs


def probe_budget(probe: dict) -> int:
    return int(probe.get("initialDelaySeconds", 0)) + int(probe.get("periodSeconds", 10)) * int(probe.get("failureThreshold", 3))


def window_for(docs: list[dict]) -> int:
    w = MIN_WINDOW
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        for c in d["spec"]["template"]["spec"].get("containers", []):
            startup = probe_budget(c["startupProbe"]) if "startupProbe" in c else 0
            other = max([probe_budget(c[k]) for k in ("readinessProbe", "livenessProbe") if k in c] or [0])
            w = max(w, startup + other + SLACK)
    return w


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch_via_port_forward(ns: str, pod: str, port: int, path: str) -> str:
    local = free_port()
    pf = subprocess.Popen(["kubectl", "-n", ns, "port-forward", f"pod/{pod}", f"{local}:{port}"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(2)
        p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5",
                            f"http://127.0.0.1:{local}{path}"], capture_output=True, text=True)
        code = p.stdout.strip()
        if code in ("", "000"):
            return "connection_refused"
        return code
    finally:
        pf.terminate()
        try:
            pf.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pf.kill()


def resolve_port(port, container: dict) -> int | None:
    if isinstance(port, int):
        return port
    if isinstance(port, str) and port.isdigit():
        return int(port)
    for cp in container.get("ports", []) or []:
        if cp.get("name") == port:
            return int(cp["containerPort"])
    return None


def observe(unit: dict, out: Path) -> dict:
    case_dir: Path = unit["case_dir"]
    cid = case_dir.name
    ns = unit["ns"]
    manifests = sorted((case_dir / "repo" / "k8s").glob("*.y*ml"))
    if unit.get("input"):
        manifests.append(case_dir / "runtime" / unit["input"])
    docs = load_docs(manifests)
    window = window_for(docs)
    rec: dict = {"case": cid, "input": unit.get("input"), "namespace": ns, "window_seconds": window}
    t0 = time.time()
    run(["kubectl", "create", "namespace", ns])
    for m in manifests:
        run(["kubectl", "-n", ns, "apply", "-f", str(m)])
    timeline = []
    first_ready = None
    pod = None
    while time.time() - t0 < window:
        p = run(["kubectl", "-n", ns, "get", "pods", "-o", "json"], check=False)
        try:
            items = json.loads(p.stdout).get("items", []) if p.returncode == 0 else []
        except json.JSONDecodeError:
            items = []
        items = [x for x in items if not x["metadata"].get("deletionTimestamp")]
        if items:
            pod_obj = items[0]
            pod = pod_obj["metadata"]["name"]
            conds = {c["type"]: c["status"] for c in pod_obj.get("status", {}).get("conditions", [])}
            cs = (pod_obj.get("status", {}).get("containerStatuses") or [{}])[0]
            ready = conds.get("Ready") == "True"
            if ready and first_ready is None:
                first_ready = round(time.time() - t0, 1)
            timeline.append({"t": round(time.time() - t0, 1), "ready": ready, "restarts": cs.get("restartCount", 0),
                             "started": cs.get("started")})
        time.sleep(5)
    rec["timeline"] = timeline
    last = timeline[-1] if timeline else {}
    rec["pod"] = pod
    rec["ready_at_end"] = bool(last.get("ready"))
    rec["first_ready_seconds"] = first_ready
    rec["restart_count"] = last.get("restarts", 0)
    rec["started"] = last.get("started")
    # Ready throughout the last SLACK seconds of the window
    tail = [x for x in timeline if x["t"] >= window - SLACK]
    rec["ready_stable_tail"] = bool(tail) and all(x["ready"] for x in tail)
    ev = run(["kubectl", "-n", ns, "get", "events", "-o", "json"], check=False)
    events = []
    if ev.returncode == 0:
        for e in json.loads(ev.stdout).get("items", []):
            if e.get("reason") in ("Unhealthy", "Killing", "BackOff", "Failed", "ErrImageNeverPull"):
                events.append({"reason": e.get("reason"), "message": (e.get("message") or "")[:300], "count": e.get("count")})
    rec["events"] = events
    probes = []
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        for c in d["spec"]["template"]["spec"].get("containers", []):
            for kind, key in (("startup", "startupProbe"), ("readiness", "readinessProbe"), ("liveness", "livenessProbe")):
                pr = c.get(key)
                if not pr:
                    continue
                if "httpGet" not in pr:
                    probes.append({"kind": kind, "type": "non-http"})
                    continue
                hg = pr["httpGet"]
                port = resolve_port(hg.get("port"), c)
                path = hg.get("path", "/")
                status = None
                if pod and port is not None and hg.get("scheme", "HTTP") == "HTTP":
                    status = fetch_via_port_forward(ns, pod, port, path)
                probes.append({"kind": kind, "declared_port": hg.get("port"), "port": port, "path": path,
                               "http_status": status})
    rec["probes"] = probes
    logs = run(["kubectl", "-n", ns, "logs", f"pod/{pod}", "--tail", "60"], check=False) if pod else None
    lp = out / "logs" / f"{cid}{('-' + unit['input']) if unit.get('input') else ''}.log"
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text((logs.stdout + logs.stderr) if logs else "")
    http_ok = all(p.get("http_status") == "200" for p in probes if p.get("type") != "non-http")
    rec["runtime_class"] = "ALIGNED" if (rec["ready_stable_tail"] and rec["restart_count"] == 0 and http_ok) else "MISALIGNED"
    rec["observed_seconds"] = round(time.time() - t0, 1)
    run(["kubectl", "delete", "namespace", ns, "--wait=false"], check=False)
    return rec


def build(case_dir: Path, out: Path) -> dict:
    cid = case_dir.name
    repo = case_dir / "repo"
    work = out / "build" / cid
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(repo, work)
    t0 = time.time()
    rec = {"case": cid}
    try:
        run(["mvn", "-q", "-B", "-DskipTests", "package"], cwd=work, timeout=900)
        run(["docker", "build", "-q", "-t", f"fifthnine-bench/{cid}:1", "."], cwd=work, timeout=600)
        run(["kind", "load", "docker-image", f"fifthnine-bench/{cid}:1", "--name", os.environ.get("KIND_CLUSTER", "bench")], timeout=600)
        rec["build"] = "ok"
    except Exception as e:  # noqa: BLE001 - recorded, case marked undecided
        rec["build"] = "failed"
        rec["error"] = str(e)[-3000:]
    rec["build_seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", default="1/1")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    i, n = (int(x) for x in a.shard.split("/"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = sorted(p for p in Path(a.cases).iterdir() if (p / "repo").is_dir())
    cases = [c for k, c in enumerate(cases) if k % n == i - 1]
    builds = {c.name: build(c, out) for c in cases}
    units = []
    for c in cases:
        if builds[c.name]["build"] != "ok":
            continue
        meta = yaml.safe_load((c / "case.yaml").read_text()) or {}
        inputs = [ri if isinstance(ri, str) else (ri.get("file") or ri.get("path")) for ri in (meta.get("runtime_inputs") or [])]
        inputs = [Path(x).name for x in inputs if x]
        if inputs:
            for k, inp in enumerate(inputs):
                units.append({"case_dir": c, "input": inp, "ns": f"{c.name}-r{k + 1}"})
        else:
            units.append({"case_dir": c, "input": None, "ns": c.name})
    results = []
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        for r in ex.map(lambda u: _safe_observe(u, out), units):
            results.append(r)
            print(json.dumps({k: r.get(k) for k in ("case", "input", "runtime_class", "ready_at_end", "restart_count")}), flush=True)
    report = {"shard": a.shard, "builds": list(builds.values()), "runs": results, "versions": versions()}
    (out / f"runtime-shard-{i}-of-{n}.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _safe_observe(u: dict, out: Path) -> dict:
    try:
        return observe(u, out)
    except Exception as e:  # noqa: BLE001
        return {"case": u["case_dir"].name, "input": u.get("input"), "runtime_class": "UNDECIDED", "error": str(e)[-3000:]}


def versions() -> dict:
    v = {}
    for name, cmd in (("kind", ["kind", "version"]), ("kubectl", ["kubectl", "version", "-o", "json"]),
                      ("java", ["java", "-version"]), ("mvn", ["mvn", "-v"]), ("docker", ["docker", "version", "--format", "{{.Server.Version}}"])):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            v[name] = (p.stdout + p.stderr).strip()[:600]
        except Exception as e:  # noqa: BLE001
            v[name] = f"unavailable: {e}"
    return v


if __name__ == "__main__":
    sys.exit(main())
