"""Runs inside a case repository with the real libraries installed and reports what they resolved, as JSON on stdout.

This script imports and inspects the repository's application code. It is runtime tooling for the M2-3 comparison only; the
scanner never imports or runs repository code. Usage: python probe.py SPEC_JSON (cwd is the case repository).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from typing import Any


def _routes(module: str, attr: str, wanted: list[list[str]]) -> dict[str, Any]:
    """Send each route the scan reports to the application (TestClient, in process) and record the status; 404 or 405 means the
    application does not serve it. Also list the operations in the OpenAPI schema, to compare the route count."""
    import warnings

    warnings.simplefilter("ignore")
    from starlette.routing import Match

    app = getattr(importlib.import_module(module), attr)
    status: dict[str, int | str] = {}
    for method, path in wanted:  # the router's own matching, as for a request; no handler runs
        url = re.sub(r"\{[^}]+\}", "1", path) or "/"
        scope = {"type": "http", "path": url, "method": method, "root_path": "", "headers": [], "query_string": b""}
        best = Match.NONE
        for r in app.router.routes:
            m, _child = r.matches(scope)
            if m == Match.FULL:
                best = m
                break
            if m == Match.PARTIAL:
                best = m
        status[f"{method}:{path}"] = {Match.FULL: 200, Match.PARTIAL: 405, Match.NONE: 404}[best]
    ops = sorted(f"{m.upper()}:{p}" for p, v in app.openapi().get("paths", {}).items() for m in v)
    return {"status": status, "openapi": ops, "debug": bool(app.debug)}


def _settings(dotted: str, field: str, env: str | None) -> dict[str, Any]:
    mod, cls = dotted.rsplit(".", 1)
    klass = getattr(importlib.import_module(mod), cls)
    info = klass.model_fields[field]
    out: dict[str, Any] = {"default": None if info.is_required() else info.default, "required": info.is_required()}
    if env:  # does the library bind this exact name (in another case) to the field?
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.upper() == env.upper()}
        os.environ[env.lower()] = "123"
        try:
            value = klass.model_construct() if False else klass(**({} if not info.is_required() else {}))
            out["env_binds"] = str(getattr(value, field)) in ("123", "123.0")
        except Exception as exc:  # noqa: BLE001 - a required field without the variable
            out["env_binds"] = f"error: {type(exc).__name__}"
        finally:
            os.environ.pop(env.lower(), None)
            os.environ.update(saved)
    return out


def _strategy(obj: Any) -> dict[str, Any]:
    import tenacity

    name = type(obj).__name__
    if isinstance(obj, tenacity.stop_any):
        return {"strategy": "any", "of": [_strategy(s) for s in obj.stops]}
    if isinstance(obj, tenacity.stop_all):
        return {"strategy": "all", "of": [_strategy(s) for s in obj.stops]}
    if name == "_stop_never":
        return {"strategy": "stop_never"}
    if isinstance(obj, tenacity.stop_after_attempt):
        return {"strategy": "stop_after_attempt", "attempts": obj.max_attempt_number}
    if isinstance(obj, tenacity.stop_after_delay):
        return {"strategy": "stop_after_delay", "seconds": obj.max_delay}
    if isinstance(obj, tenacity.wait_none):
        return {"strategy": "wait_none"}
    if isinstance(obj, tenacity.wait_fixed):
        return {"strategy": "wait_fixed", "seconds": obj.wait_fixed}
    if isinstance(obj, tenacity.wait_random):
        return {"strategy": "wait_random", "min": obj.wait_random_min, "max": obj.wait_random_max}
    if isinstance(obj, tenacity.wait_exponential):
        return {"strategy": type(obj).__name__, "multiplier": obj.multiplier, "max": obj.max, "exp_base": obj.exp_base, "min": obj.min}
    if isinstance(obj, tenacity.wait_combine):
        return {"strategy": "sum", "of": [_strategy(w) for w in obj.wait_funcs]}
    return {"strategy": name}


def _retry(dotted: str) -> dict[str, Any]:
    mod, fn = dotted.rsplit(".", 1)
    f = getattr(importlib.import_module(mod), fn)
    r = f.retry
    return {"stop": _strategy(r.stop), "wait": _strategy(r.wait)}


def _client(dotted: str) -> dict[str, Any]:
    mod, name = dotted.rsplit(".", 1)
    c = getattr(importlib.import_module(mod), name)
    t = c.timeout
    return {"connect": t.connect, "read": t.read, "write": t.write, "pool": t.pool}


def _gunicorn(argv: list[str]) -> dict[str, Any]:
    p = subprocess.run([sys.executable, "-m", "gunicorn", "--print-config", *argv], capture_output=True, text=True, timeout=60)
    out: dict[str, Any] = {}
    for line in p.stdout.splitlines():
        k, sep, v = line.partition(" = ")
        if sep:
            out[k.strip()] = v.strip()
    if not out:
        out["error"] = (p.stderr or p.stdout)[-500:]
    return out


def _uvicorn(argv: list[str]) -> dict[str, Any]:
    from uvicorn.main import main

    ctx = main.make_context("uvicorn", list(argv))
    return {k: ctx.params.get(k) for k in ("host", "port", "timeout_graceful_shutdown", "reload", "workers")}


def main() -> None:
    spec = json.loads(sys.argv[1])
    sys.path.insert(0, os.getcwd())
    out: dict[str, Any] = {}
    for key, (kind, *args) in spec.items():
        try:
            out[key] = {"routes": _routes, "settings": _settings, "retry": _retry, "client": _client, "gunicorn": _gunicorn,
                        "uvicorn": _uvicorn}[kind](*args)
        except Exception as exc:  # noqa: BLE001 - recorded per item
            out[key] = {"error": f"{type(exc).__name__}: {exc}"[:400]}
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
