"""M2-3 runtime cases. Each case is a small FastAPI repository (pyproject, Dockerfile, Deployment, app package) that the scanner
reads and the real libraries then load. `env` is the container env the Deployment declares; the probe runs with the same values."""

# ruff: noqa: E501  (case data)

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

PYPROJECT = """[project]
name = "svc"
version = "1.0"
dependencies = ["fastapi==0.159.1", "uvicorn==0.54.0", "gunicorn==26.2.0", "httpx==0.28.1", "tenacity==9.2.1", "pydantic-settings==2.11.0"]
"""
DOCKERFILE = "FROM python:3.12-slim\nWORKDIR /srv\nCOPY . .\nRUN pip install .\n{cmd}"
DEPLOY = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: svc
spec:
  selector:
    matchLabels: {{app: svc}}
  template:
    metadata:
      labels: {{app: svc}}
    spec:
      containers:
        - name: api
          image: registry.example/svc:1.0
{command}{env}"""
MAIN = 'from fastapi import FastAPI\n\napp = FastAPI(title="svc")\n'


@dataclass
class Case:
    name: str
    files: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    args: list[str] | None = None
    env: dict[str, str | None] = field(default_factory=dict)
    run_env: dict[str, str] = field(default_factory=dict)
    cmd: str = 'CMD ["uvicorn", "app.main:app"]\n'
    sample: str | None = None
    covers: str = ""


def write_case(d: Path, c: Case) -> None:
    (d / "pyproject.toml").write_text(PYPROJECT)
    (d / "Dockerfile").write_text(DOCKERFILE.format(cmd=c.cmd))
    for rel, text in {"app/__init__.py": "", "app/main.py": MAIN, **c.files}.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"))
    cmd = ""
    if c.command is not None:
        cmd += f"          command: {json.dumps(c.command)}\n"
    if c.args is not None:
        cmd += f"          args: {json.dumps(c.args)}\n"
    env = ""
    if c.env:
        env = "          env:\n" + "".join(f"            - name: {k}\n              value: {json.dumps(v)}\n" for k, v in c.env.items() if v is not None)
    (d / "deploy.yaml").write_text(DEPLOY.format(command=cmd, env=env))


ROUTES = {
    "app/main.py": 'from fastapi import FastAPI\nfrom app import api\n\napp = FastAPI()\napp.include_router(api.router, prefix="/v2")\n\n@app.get("/healthz")\ndef h():\n    return {}\n',
    "app/api.py": 'from fastapi import APIRouter\n\nrouter = APIRouter(prefix="/items")\n\n@router.api_route("/{i}", methods=["GET", "PUT"])\ndef item(i: int):\n    return {}\n\n@router.delete("")\ndef d():\n    return {}\n',
}
SETTINGS = {"app/settings.py": """
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="SVC_")
        budget: float = 2.5
        url: str = Field("http://x", alias="UPSTREAM_URL")
        retries: int = 3

    class Plain(BaseSettings):
        api_base: str = "http://api:8080"
        timeout_s: float = 1.5
    """}
RETRY = {"app/r.py": """
    import httpx
    import tenacity
    from tenacity import retry, stop_after_attempt, stop_after_delay, wait_exponential, wait_fixed, wait_random

    client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0))
    plain = httpx.Client()
    tup = httpx.Client(timeout=(1.0, 2.0, 3.0, 4.0))

    @retry(stop=stop_after_attempt(5) | stop_after_delay(10), wait=wait_random(1, 3))
    async def a():
        return await client.get("/a")

    @tenacity.retry
    def b():
        return plain.get("/b")

    @retry(stop=stop_after_delay(7), wait=wait_exponential(multiplier=2, max=20) + wait_fixed(1))
    def c():
        return tup.get("/c")

    @retry(wait=wait_exponential(0.5, 30))
    def d():
        return 1
    """}

CASES: list[Case] = [
    Case("sample-catalog", sample="catalog-api-fastapi", run_env={}, covers="the answer-key sample: gunicorn flags, routes, settings, tenacity, httpx"),
    Case("routes-prefixes", files=ROUTES, command=["uvicorn", "app.main:app"], covers="include prefix + router prefix, api_route methods, empty path"),
    Case("debug-true", files={"app/main.py": "from fastapi import FastAPI\napp = FastAPI(debug=True)\n"}, command=["uvicorn", "app.main:app"], covers="debug literal"),
    Case("debug-assigned-later", files={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\napp.debug = True\n"}, command=["uvicorn", "app.main:app"], covers="app.debug assignment"),
    Case("gunicorn-flags", command=["gunicorn"], args=["app.main:app", "-k", "uvicorn_worker.UvicornWorker", "-w4", "--timeout", "120", "-b", "0.0.0.0:9000"], covers="short and long flags"),
    Case("gunicorn-defaults", command=["gunicorn", "app.main:app", "-k", "uvicorn_worker.UvicornWorker"], covers="gunicorn defaults without PORT and WEB_CONCURRENCY"),
    Case("gunicorn-two-binds", command=["gunicorn", "app.main:app", "-k", "uvicorn_worker.UvicornWorker", "--bind=0.0.0.0:8000", "--bind", "[::]:8000"], covers="repeated bind"),
    Case("uvicorn-image-cmd", cmd='CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port=8080"]\n', covers="image CMD, host and port"),
    Case("uvicorn-env-port", command=["uvicorn", "app.main:app"], env={"UVICORN_PORT": "9001"}, covers="click auto envvar"),
    Case("uvicorn-cli-over-env", command=["uvicorn", "app.main:app", "--port", "9100"], env={"UVICORN_PORT": "9001"}, covers="command line beats UVICORN_PORT"),
    Case("uvicorn-graceful", command=["uvicorn", "app.main:app", "--timeout-graceful-shutdown", "25"], covers="timeout_graceful_shutdown"),
    Case("python-m-uvicorn", command=["python", "-m", "uvicorn", "app.main:app", "--port", "7000"], covers="python -m launcher"),
    Case("sh-c-gunicorn", command=["sh", "-c", "exec gunicorn app.main:app -k uvicorn_worker.UvicornWorker --timeout=${T:-45}"], covers="shell default parameter"),
    Case("settings-prefix-alias", files=SETTINGS, command=["uvicorn", "app.main:app"], covers="env_prefix, alias, plain names"),
    Case("tenacity-httpx", files=RETRY, command=["uvicorn", "app.main:app"], covers="stop combinations, wait sums, positional wait args, httpx Timeout forms"),
]
