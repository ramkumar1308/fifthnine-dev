"""M2-D3 driver: build and run each case, scan it offline, and compare the scan with what the JVM resolved.

Needs a JDK 17+, Maven and network access to Maven Central, which is why it is not part of the offline test suite: it runs in its own
workflow (.github/workflows/spring-runtime.yml). The scan itself stays offline; only the build and the fixture application use the network.

    python tests/runtime/spring/run_cases.py --out DIR [--only NAME ...] [--list]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from fifthnine.core.engine import ScanOptions, run_scan  # noqa: E402
from tests.runtime.spring.cases import CASES, DEFAULT_ENTRYPOINT, Case  # noqa: E402
from tests.runtime.spring.compare import CaseResult, Check, absent_probes, compare, endpoint_probes, runtime_keys  # noqa: E402
from tests.unit.test_m2_2_spring import Repo  # noqa: E402

POM_EXTRA = """  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin>
    </plugins>
  </build>
"""
START_TIMEOUT = 150
BUILD_TIMEOUT = 600


def _repo(case: Case) -> Repo:
    docker_env = "".join(f"ENV {item}\n" for item in case.docker_env)
    entry = f"ENTRYPOINT {json.dumps(case.entrypoint)}\n" if case.entrypoint else 'ENTRYPOINT ["java", "-jar", "/app/app.jar"]\n'
    return Repo(boot=case.boot, resources=case.resources, env=dict(case.env), command=case.args, docker_env=docker_env, entrypoint=entry,
                pom_extra=POM_EXTRA)


def _get(port: int, path: str) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:  # noqa: S310 - loopback only
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return None, ""


def _run_env(case: Case, out: Path, keys: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "JAVA_HOME", "LANG", "TMPDIR") if k in os.environ}
    for item in case.docker_env:
        k, _, v = item.partition("=")
        env[k] = v
    env.update({k: v for k, v in case.env.items() if v is not None})  # container env overrides Dockerfile ENV
    env.update(case.run_env)
    if case.kubernetes:  # what the kubelet puts in every pod; Spring Boot reads these to detect Kubernetes
        env.update({"KUBERNETES_SERVICE_HOST": "10.96.0.1", "KUBERNETES_SERVICE_PORT": "443"})
    env.update({"FN_RUNTIME_OUT": str(out), "FN_RUNTIME_KEYS": str(keys)})
    return env


def prepare(case: Case, root: Path) -> tuple[Path, list[Any], float]:
    """Write the case's repository and scan it (offline). Returns the working directory, the Spring facts and the scan seconds."""
    work = root / "work" / case.name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    _repo(case).write(work)
    src = work / "src/main/java/fixture"
    src.mkdir(parents=True)
    shutil.copy(HERE / "fixture_app/Application.java", src / "Application.java")
    t0 = time.time()
    result = run_scan(ScanOptions(path=work, profile=case.profile, facts="all"))
    return work, [f for f in result.facts if f.kind.startswith("spring.")], time.time() - t0


def run_case(case: Case, root: Path) -> tuple[CaseResult, dict[str, Any]]:
    work, facts, scan_s = prepare(case, root)
    keys = work / "fn-keys.txt"
    keys.write_text("\n".join(runtime_keys(facts)) + "\n", encoding="utf-8")
    out = work / "fn-runtime.json"
    log = root / "logs" / f"{case.name}.log"
    log.parent.mkdir(exist_ok=True)
    info: dict[str, Any] = {"case": case.name, "boot": case.boot, "scan_s": round(scan_s, 2), "facts": len(facts)}
    res = CaseResult(case.name)
    with log.open("w") as lf:
        build = subprocess.run(["mvn", "-q", "-B", "-DskipTests", "package"], cwd=work, stdout=lf, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT)  # noqa: S603, S607
    if build.returncode != 0:
        res.checks.append(Check(case.name, "build", case.name, False, "mvn package", build.returncode, f"see {log.name}"))
        return res, info
    jars = [j for j in glob.glob(str(work / "target/app-*.jar")) if not j.endswith(".original")]
    argv = [jars[0] if a == "/app/app.jar" else a for a in (case.entrypoint or DEFAULT_ENTRYPOINT)] + list(case.args or [])
    with log.open("a") as lf:
        proc = subprocess.Popen(argv, cwd=work, env=_run_env(case, out, keys), stdout=lf, stderr=subprocess.STDOUT)  # noqa: S603
        try:
            deadline = time.time() + START_TIMEOUT
            while not out.exists() and proc.poll() is None and time.time() < deadline:
                time.sleep(0.5)
            if not out.exists():
                res.checks.append(Check(case.name, "start", case.name, False, "application started", f"exit {proc.poll()}", f"see {log.name}"))
                return res, info
            rt = json.loads(out.read_text(encoding="utf-8"))
            probes = set(endpoint_probes(facts)) | set(absent_probes(facts, rt.get("serverPort")))
            http: dict[tuple[int, str], int | None] = {p: _get(*p)[0] for p in sorted(probes)}
            discovery = _discovery(facts)
            res = compare(case.name, facts, rt, http, discovery)
            info["runtime_values"] = {k: v.get("value", v.get("list")) for k, v in rt.get("values", {}).items()}
            info["origins"] = {k: v.get("origin") for k, v in rt.get("values", {}).items()}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    return res, info


def _discovery(facts: list[Any]) -> list[str] | None:
    """Endpoint ids the discovery page lists, taken from the page under the first present health endpoint's parent path."""
    for f in facts:
        if f.kind == "spring.actuator.endpoint" and isinstance(f.value, dict):
            parent = str(f.value["path"]).rsplit("/", 2)[0]
            status, body = _get(int(f.value["port"]), parent)
            if status == 200:
                try:
                    links = json.loads(body).get("_links", {})
                except ValueError:
                    return None
                return sorted(k for k in links if k != "self" and not k.endswith("-path") and "{" not in str(links[k].get("href", "")))
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("runtime-out"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--list", action="store_true")
    ns = ap.parse_args()
    cases = [c for c in CASES if not ns.only or c.name in ns.only]
    if ns.list:
        print("\n".join(f"{c.name}\t{c.boot}\t{c.covers}" for c in CASES))
        return 0
    ns.out.mkdir(parents=True, exist_ok=True)
    report: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    failed = 0
    for case in cases:
        t0 = time.time()
        try:
            res, info = run_case(case, ns.out)
        except Exception as exc:  # noqa: BLE001 - one broken case must not hide the others
            res, info = CaseResult(case.name), {"case": case.name, "error": repr(exc)}
            res.checks.append(Check(case.name, "harness", case.name, False, "case runs", repr(exc)))
        if not res.checks:  # a case that compares nothing must not pass
            res.checks.append(Check(case.name, "harness", case.name, False, "at least one check", 0, "no check was made"))
        info["seconds"] = round(time.time() - t0, 1)
        info["checks"] = len(res.checks)
        info["failures"] = [c.__dict__ for c in res.failures]
        info["skipped"] = dict(res.skipped)
        info["all_checks"] = [c.__dict__ for c in res.checks]
        report.append(info)
        totals["checks"] += len(res.checks)
        totals["failures"] += len(res.failures)
        failed += bool(res.failures)
        for k, v in res.skipped.items():
            totals[f"skipped: {k}"] += v
        print(f"{'FAIL' if res.failures else 'ok  '} {case.name}: {len(res.checks)} checks, {len(res.failures)} failures, {info['seconds']} s", flush=True)
        for c in res.failures:
            print(f"     {c.kind} {c.subject}: scan says {c.expected!r}, runtime {c.runtime!r} {c.note}", flush=True)
    (ns.out / "report.json").write_text(json.dumps({"cases": report, "totals": dict(totals)}, indent=1, default=str), encoding="utf-8")
    print(json.dumps(dict(totals), indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
