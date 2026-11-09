"""M2-3: Python application facts (FastAPI app and routes, gunicorn and uvicorn settings, pydantic-settings fields, tenacity
retries, httpx client timeouts, route deadlines and calls). Each test writes a small repository and scans it offline."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from fifthnine.core.engine import ScanOptions, run_scan
from fifthnine.schema.v1 import Fact, FactStatus, OverrideExposure, ScanResult
from tests.support.answer_keys import FactKey, load
from tests.support.scorer import score

from ..conftest import ROOT

PYPROJECT = """[project]
name = "svc"
version = "1.0"
dependencies = [
    "fastapi=={fastapi}",
    "uvicorn==0.54.0",
    "gunicorn==26.2.0",
    "httpx==0.28.1",
    "tenacity=={tenacity}",
    "pydantic-settings==2.11.0",
]
"""
DOCKERFILE = """FROM python:3.12-slim
WORKDIR /srv
COPY . .
RUN pip install .
{cmd}"""
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


def _repo(tmp: Path, files: dict[str, str], *, command: list[str] | None = None, args: list[str] | None = None, env: dict[str, str | None] | None = None,
          secret: bool = False, cmd: str = 'CMD ["uvicorn", "app.main:app"]\n', deploy: bool = True, fastapi: str = "0.159.1",
          tenacity: str = "9.2.1") -> ScanResult:
    (tmp / "pyproject.toml").write_text(PYPROJECT.format(fastapi=fastapi, tenacity=tenacity))
    (tmp / "Dockerfile").write_text(DOCKERFILE.format(cmd=cmd))
    for rel, text in {"app/__init__.py": "", "app/main.py": MAIN, **files}.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"))
    if deploy:
        c = ""
        if command is not None:
            c += f"          command: {command!r}\n".replace("'", '"')
        if args is not None:
            c += f"          args: {args!r}\n".replace("'", '"')
        e = ""
        if env:
            e = "          env:\n" + "".join(
                f"            - name: {k}\n              value: \"{v}\"\n" if v is not None else
                f"            - name: {k}\n              valueFrom:\n                secretKeyRef: {{name: s, key: k}}\n" for k, v in env.items())
        if secret:
            e += "          envFrom:\n            - secretRef: {name: svc-env}\n"
        (tmp / "deploy.yaml").write_text(DEPLOY.format(command=c, env=e))
    return run_scan(ScanOptions(path=tmp, facts="all"))


def _f(r: ScanResult, kind: str, subject: str) -> Fact:
    hits = [f for f in r.facts if f.kind == kind and f.subject.key == subject]
    assert len(hits) == 1, (kind, subject, sorted(f.subject.key for f in r.facts if f.kind == kind))
    return hits[0]


def _all(r: ScanResult, kind: str) -> dict[str, Fact]:
    return {f.subject.key: f for f in r.facts if f.kind == kind}


CSUB = "k8s:Deployment/_/svc/api"


# ---------------------------------------------------------------- the sample, scored against its answer key


def test_catalog_sample_scores_correct_against_the_key() -> None:
    root = ROOT / "samples" / "catalog-api-fastapi"
    r = run_scan(ScanOptions(path=root, facts="all"))
    key = load(ROOT / "benchmarks" / "dev" / "facts" / "catalog-api-fastapi.facts.yaml", FactKey)
    s = score(key, list(r.facts), r.linkages, "M2-3")
    assert s.passed, s.report()
    assert not s.violated
    assert len(s.correct) == sum(k.milestone in ("M2-1", "M2-2", "M2-3") for k in key.facts)


# ---------------------------------------------------------------- application and routes


def test_app_and_routes_with_router_prefixes(tmp_path: Path) -> None:
    r = _repo(tmp_path, {
        "app/main.py": 'from fastapi import FastAPI\nfrom app import api\n\napp = FastAPI()\napp.include_router(api.router, prefix="/v2")\n\n'
                       '@app.get("/healthz")\ndef h():\n    return {}\n',
        "app/api.py": 'from fastapi import APIRouter\n\nrouter = APIRouter(prefix="/items")\n\n@router.api_route("/{i}", methods=["GET", "PUT"])\n'
                      'def item(i: int):\n    return {}\n'})
    routes = _all(r, "fastapi.route")
    assert set(routes) == {"python:app.main:app#GET:/healthz", "python:app.main:app#GET:/v2/items/{i}", "python:app.main:app#PUT:/v2/items/{i}"}
    assert routes["python:app.main:app#GET:/v2/items/{i}"].inputs[0].source.line_start == 5  # type: ignore[union-attr]
    assert _f(r, "fastapi.route_table", "python:app.main:app").value == {"complete": True, "routes": 3, "documentation_routes": "not listed"}
    assert _f(r, "python.asgi_app", "python:app.main:app").value == {"framework": "fastapi", "module": "app.main", "attribute": "app"}


def test_dynamic_registration_makes_the_route_table_incomplete(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/main.py": 'from fastapi import FastAPI\nfrom app import api\n\napp = FastAPI()\n'
                                        'for p in ("/a", "/b"):\n    app.include_router(api.router, prefix=p)\napp.mount("/static", object())\n',
                         "app/api.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n'})
    t = _f(r, "fastapi.route_table", "python:app.main:app")
    assert t.value["complete"] is False
    assert any("route table may be incomplete" in c.message for c in r.coverage)


def test_root_path_makes_the_route_table_incomplete(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/main.py": 'from fastapi import FastAPI\napp = FastAPI(root_path="/svc")\n'})
    assert _f(r, "fastapi.route_table", "python:app.main:app").value["complete"] is False


def test_factory_application_is_unresolved(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/main.py": 'from fastapi import FastAPI\n\ndef create():\n    return FastAPI()\n\napp = create()\n'})
    f = _f(r, "python.asgi_app", "python:app.main:app")
    assert f.status is FactStatus.UNRESOLVED
    assert not _all(r, "fastapi.route")


def test_debug_literal_default_and_unresolved(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    r = _repo(tmp_path / "a", {"app/main.py": "from fastapi import FastAPI\napp = FastAPI(debug=True)\n"})
    assert _f(r, "fastapi.debug", "python:app.main:app").value is True
    r = _repo(tmp_path / "b", {})
    d = _f(r, "fastapi.debug", "python:app.main:app")
    assert d.status is FactStatus.DEFAULT and d.value is False and d.default_ref is not None
    r = _repo(tmp_path / "c", {"app/main.py": "import os\nfrom fastapi import FastAPI\napp = FastAPI(debug=os.environ.get('D') == '1')\n"})
    assert _f(r, "fastapi.debug", "python:app.main:app").status is FactStatus.UNRESOLVED


def test_default_needs_a_verified_version(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, fastapi="0.99.1")
    d = _f(r, "fastapi.debug", "python:app.main:app")
    assert d.status is FactStatus.UNRESOLVED and "no cited default" in (d.unresolved_reason or "")


def test_unlinked_root_still_gets_source_facts(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/s.py": "from pydantic_settings import BaseSettings\nclass S(BaseSettings):\n    port: int = 8000\n"}, deploy=False)
    f = _f(r, "python.settings_field", "python:app.s.S#port")
    assert f.override_exposure is OverrideExposure.ENV_POSSIBLE  # no container is linked: the environment is unknown
    assert not _all(r, "python.asgi_app")


# ---------------------------------------------------------------- gunicorn and uvicorn


def test_gunicorn_flags_and_defaults(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["gunicorn"], args=["app.main:app", "-k", "uvicorn_worker.UvicornWorker", "-w4", "--timeout", "120"])
    g = _all(r, "gunicorn.setting")
    assert g[f"{CSUB}#worker_class"].value == "uvicorn_worker.UvicornWorker"
    assert g[f"{CSUB}#workers"].value == 4 and g[f"{CSUB}#timeout"].value == 120
    gt = g[f"{CSUB}#graceful_timeout"]
    assert gt.status is FactStatus.DEFAULT and gt.value == 30 and gt.override_exposure is OverrideExposure.NONE
    assert g[f"{CSUB}#bind"].value == "127.0.0.1:8000"


def test_gunicorn_defaults_are_exposed_to_an_external_env_source(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["gunicorn", "app.main:app"], secret=True)
    gt = _f(r, "gunicorn.setting", f"{CSUB}#graceful_timeout")
    assert gt.override_exposure is OverrideExposure.ENV_POSSIBLE
    assert gt.exposure_sources[0].variables == ["GUNICORN_CMD_ARGS"]
    bind = _f(r, "gunicorn.setting", f"{CSUB}#bind")
    assert "PORT" in bind.exposure_sources[0].variables


def test_gunicorn_bind_default_depends_on_port(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["gunicorn", "app.main:app"], env={"PORT": "9000", "WEB_CONCURRENCY": "3"})
    assert _f(r, "gunicorn.setting", f"{CSUB}#bind").status is FactStatus.UNRESOLVED
    assert _f(r, "gunicorn.setting", f"{CSUB}#workers").status is FactStatus.UNRESOLVED


def test_gunicorn_cmd_args_in_the_repository_is_read_below_the_command_line(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["gunicorn", "app.main:app", "--workers=2"], env={"GUNICORN_CMD_ARGS": "--timeout 5 --workers 9"})
    t = _f(r, "gunicorn.setting", f"{CSUB}#timeout")
    assert t.value == 5 and t.precedence == "gunicorn.env"
    assert _f(r, "gunicorn.setting", f"{CSUB}#workers").value == 2  # the command line wins


def test_uvicorn_from_the_image_cmd(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, cmd='CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port=8080"]\n')
    u = _all(r, "uvicorn.setting")
    assert u[f"{CSUB}#host"].value == "0.0.0.0" and u[f"{CSUB}#host"].status is FactStatus.INFERRED
    assert u[f"{CSUB}#port"].value == 8080
    tg = u[f"{CSUB}#timeout_graceful_shutdown"]
    assert tg.status is FactStatus.DEFAULT and tg.value is None


def test_uvicorn_env_var_sets_a_setting(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["uvicorn", "app.main:app"], env={"UVICORN_PORT": "9001"})
    p = _f(r, "uvicorn.setting", f"{CSUB}#port")
    assert p.value == 9001 and p.precedence == "uvicorn.env"


def test_python_m_launcher(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["python", "-m", "uvicorn", "app.main:app", "--port", "7000"])
    assert _f(r, "uvicorn.setting", f"{CSUB}#port").value == 7000


def test_shell_wrapped_command_is_expanded_or_skipped(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    r = _repo(tmp_path / "a", {}, command=["sh", "-c", "exec gunicorn app.main:app --timeout=${T:-45}"])
    assert _f(r, "gunicorn.setting", f"{CSUB}#timeout").value == 45
    r = _repo(tmp_path / "b", {}, command=["sh", "-c", "cd /srv && gunicorn app.main:app"])
    assert not _all(r, "gunicorn.setting")
    assert any("shell command the scanner does not model" in c.message for c in r.coverage)


# ---------------------------------------------------------------- settings


def test_settings_prefix_alias_and_in_repository_env(tmp_path: Path) -> None:
    files = {"app/settings.py": """
        from pydantic import Field
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class Settings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="SVC_")
            budget: float = 2.5
            url: str = Field("http://x", alias="UPSTREAM_URL")
            token: str

        settings = Settings()
        """}
    r = _repo(tmp_path, files, command=["uvicorn", "app.main:app"], env={"svc_budget": "7"})
    s = _all(r, "python.settings_field")
    assert s["python:app.settings.Settings#budget"].value == {"default": 2.5, "env": "SVC_BUDGET"}
    assert s["python:app.settings.Settings#url"].value == {"default": "http://x", "env": "UPSTREAM_URL"}
    assert s["python:app.settings.Settings#token"].value == {"default": None, "env": "SVC_TOKEN", "required": True}


def test_settings_with_env_file_are_flagged(tmp_path: Path) -> None:
    files = {"app/settings.py": "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
                                "class S(BaseSettings):\n    model_config = SettingsConfigDict(env_file='.env')\n    a: int = 1\n"}
    r = _repo(tmp_path, files, command=["uvicorn", "app.main:app"])
    assert any("env file" in c.message for c in r.coverage)


# ---------------------------------------------------------------- tenacity and httpx


RETRY = """
import httpx
import tenacity
from tenacity import retry, stop_after_attempt, stop_after_delay, wait_exponential, wait_random

client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0))
plain = httpx.Client()

@retry(stop=stop_after_attempt(5) | stop_after_delay(10), wait=wait_random(1, 3))
async def a():
    return await client.get("/a")

@tenacity.retry
def b():
    return plain.get("/b")

@retry(stop=stop_after_attempt(N))
def c():
    return 1
"""


def test_tenacity_strategies_and_defaults(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/r.py": "N = 4\n" + RETRY})
    stop = _all(r, "python.retry.stop")
    assert stop["python:app.r.a"].value == {"strategy": "any", "of": [{"strategy": "stop_after_attempt", "attempts": 5},
                                                                       {"strategy": "stop_after_delay", "seconds": 10}]}
    assert stop["python:app.r.b"].status is FactStatus.DEFAULT and stop["python:app.r.b"].value == {"strategy": "stop_never"}
    assert stop["python:app.r.c"].status is FactStatus.UNRESOLVED  # a name, not a literal
    wait = _all(r, "python.retry.wait")
    assert wait["python:app.r.a"].value == {"strategy": "wait_random", "min": 1, "max": 3}
    assert wait["python:app.r.b"].value == {"strategy": "wait_none"}


def test_httpx_timeouts_for_retried_clients_only(tmp_path: Path) -> None:
    r = _repo(tmp_path, {"app/r.py": "N = 4\n" + RETRY, "app/other.py": "import httpx\nunused = httpx.Client(timeout=None)\n"})
    t = _all(r, "python.http_client.timeout")
    assert set(t) == {"python:app.r.client", "python:app.r.plain"}
    assert t["python:app.r.client"].value == {"connect": 1.0, "read": 3.0, "write": 3.0, "pool": 3.0}
    assert t["python:app.r.plain"].status is FactStatus.DEFAULT and t["python:app.r.plain"].value["read"] == 5.0


def test_httpx_timeout_without_a_default_is_unresolved(tmp_path: Path) -> None:
    files = {"app/r.py": "import httpx\nfrom tenacity import retry\nc = httpx.Client(timeout=httpx.Timeout(connect=1.0))\n"
                         "@retry\ndef f():\n    return c.get('/')\n"}
    r = _repo(tmp_path, files)
    assert _f(r, "python.http_client.timeout", "python:app.r.c").status is FactStatus.UNRESOLVED


# ---------------------------------------------------------------- handlers


HANDLERS = {
    "app/main.py": 'from fastapi import FastAPI\nfrom app import routes\n\napp = FastAPI()\napp.include_router(routes.router)\n',
    "app/settings.py": "from pydantic_settings import BaseSettings\nclass Settings(BaseSettings):\n    budget: float = 3.0\nsettings = Settings()\n",
    "app/client.py": "import httpx\nfrom tenacity import retry, stop_after_attempt\nc = httpx.AsyncClient(timeout=1.0)\n"
                     "@retry(stop=stop_after_attempt(2))\nasync def fetch():\n    return await c.get('/')\n",
    "app/routes.py": """
        import asyncio
        from fastapi import APIRouter
        from app import client
        from app.settings import settings

        router = APIRouter()

        @router.get("/a")
        async def a():
            async with asyncio.timeout(settings.budget):
                return await client.fetch()

        @router.get("/b")
        async def b():
            await client.fetch()
            async with asyncio.timeout(1.5):
                pass
        """,
}


def test_deadlines_and_calls_from_route_handlers(tmp_path: Path) -> None:
    r = _repo(tmp_path, HANDLERS, command=["uvicorn", "app.main:app"], secret=True)
    d = _all(r, "python.deadline")
    assert d["python:app.routes.a"].value == {"mechanism": "asyncio.timeout", "seconds": 3.0}
    assert d["python:app.routes.a"].override_exposure is OverrideExposure.ENV_POSSIBLE
    assert d["python:app.routes.a"].exposure_sources[0].via == "inherited"
    assert d["python:app.routes.b"].value == {"mechanism": "asyncio.timeout", "seconds": 1.5}
    calls = _all(r, "python.call_reaches")
    assert calls["python:app.routes.a"].value == {"callee": "python:app.client.fetch", "within_deadline": "python:app.routes.a"}
    assert calls["python:app.routes.b"].value == {"callee": "python:app.client.fetch", "within_deadline": None}


def test_deadline_uses_an_in_repository_env_value(tmp_path: Path) -> None:
    r = _repo(tmp_path, HANDLERS, command=["uvicorn", "app.main:app"], env={"BUDGET": "8"})
    d = _f(r, "python.deadline", "python:app.routes.a")
    assert d.value == {"mechanism": "asyncio.timeout", "seconds": 8.0}


def test_deadline_from_an_unresolved_env_value_is_unresolved(tmp_path: Path) -> None:
    r = _repo(tmp_path, HANDLERS, command=["uvicorn", "app.main:app"], env={"BUDGET": None})
    d = _f(r, "python.deadline", "python:app.routes.a")
    assert d.status is FactStatus.UNRESOLVED or d.override_exposure is OverrideExposure.ENV_POSSIBLE


def test_stack_versions_and_python_runtime(tmp_path: Path) -> None:
    r = _repo(tmp_path, {})
    v = _all(r, "stack.version")
    assert v["component:fastapi"].value == "0.159.1"
    assert v["component:python"].value == "3.12" and v["component:python"].source.path == "Dockerfile"  # type: ignore[union-attr]


def test_scan_is_deterministic(tmp_path: Path) -> None:
    def dump(res: ScanResult) -> Any:
        return [f.model_dump() for f in res.facts if f.extractor == "python-m2.3"]
    a = _repo(tmp_path, HANDLERS, command=["uvicorn", "app.main:app"])
    b = run_scan(ScanOptions(path=tmp_path, facts="all"))
    assert dump(a) == dump(b)


# ---------------------------------------------------------------- review findings (M2-3 review round 1)


def _sub(tmp: Path, name: str) -> Path:
    d = tmp / name
    d.mkdir()
    return d


def test_image_env_feeds_server_settings_and_settings_fields(tmp_path: Path) -> None:
    r = _repo(_sub(tmp_path, "a"), {}, cmd='ENV PORT=9000 WEB_CONCURRENCY=4\nCMD ["gunicorn", "app.main:app"]\n')
    assert _f(r, "gunicorn.setting", f"{CSUB}#bind").status is FactStatus.UNRESOLVED
    assert _f(r, "gunicorn.setting", f"{CSUB}#workers").status is FactStatus.UNRESOLVED
    files = dict(HANDLERS)
    r = _repo(_sub(tmp_path, "b"), files, cmd='ENV BUDGET=1\nCMD ["uvicorn", "app.main:app"]\n')
    assert _f(r, "python.deadline", "python:app.routes.a").value == {"mechanism": "asyncio.timeout", "seconds": 1.0}


def test_gunicorn_config_files_leave_settings_unresolved(tmp_path: Path) -> None:
    r = _repo(_sub(tmp_path, "a"), {"gunicorn.conf.py": "workers = 8\n"}, command=["gunicorn", "app.main:app"])
    assert _f(r, "gunicorn.setting", f"{CSUB}#workers").status is FactStatus.UNRESOLVED
    r = _repo(_sub(tmp_path, "b"), {}, command=["gunicorn", "-cconf.py", "app.main:app", "--timeout=5"])
    assert _f(r, "gunicorn.setting", f"{CSUB}#workers").status is FactStatus.UNRESOLVED
    assert _f(r, "gunicorn.setting", f"{CSUB}#timeout").value == 5  # the command line still wins


def test_unknown_gunicorn_option_makes_the_command_opaque(tmp_path: Path) -> None:
    r = _repo(tmp_path, {}, command=["gunicorn", "--made-up", "x", "app.main:app"])
    assert _f(r, "gunicorn.setting", f"{CSUB}#timeout").status is FactStatus.UNRESOLVED
    assert not _all(r, "python.asgi_app")


def test_uvicorn_socket_and_app_dir(tmp_path: Path) -> None:
    r = _repo(_sub(tmp_path, "a"), {}, command=["uvicorn", "--uds", "/tmp/s.sock", "app.main:app"])
    assert _f(r, "uvicorn.setting", f"{CSUB}#port").status is FactStatus.UNRESOLVED
    assert _f(r, "python.asgi_app", "python:app.main:app").value["module"] == "app.main"
    r = _repo(_sub(tmp_path, "b"), {}, command=["uvicorn", "--app-dir", "src", "app.main:app"])
    assert not _all(r, "python.asgi_app")


def test_routes_registered_from_another_module_or_through_app_router(tmp_path: Path) -> None:
    files = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
             "app/views.py": "from app.main import app\n@app.post('/x')\ndef x():\n    return 1\n"}
    r = _repo(_sub(tmp_path, "a"), files, command=["uvicorn", "app.main:app"])
    assert _f(r, "fastapi.route_table", "python:app.main:app").value["complete"] is False
    files = {"app/main.py": "from fastapi import FastAPI, APIRouter\nr = APIRouter()\napp = FastAPI()\napp.router.include_router(r, prefix='/h')\n"}
    r = _repo(_sub(tmp_path, "b"), files, command=["uvicorn", "app.main:app"])
    assert _f(r, "fastapi.route_table", "python:app.main:app").value["complete"] is False


def test_late_registration_before_fastapi_0_137(tmp_path: Path) -> None:
    files = {"app/main.py": "from fastapi import FastAPI, APIRouter\nrouter = APIRouter()\napp = FastAPI()\napp.include_router(router, prefix='/v1')\n"
                            "@router.get('/late')\ndef late():\n    return 1\n"}
    r = _repo(_sub(tmp_path, "a"), files, command=["uvicorn", "app.main:app"], fastapi="0.115.0")
    assert _f(r, "fastapi.route_table", "python:app.main:app").value["complete"] is False
    r = _repo(_sub(tmp_path, "b"), files, command=["uvicorn", "app.main:app"], fastapi="0.159.1")
    assert _f(r, "fastapi.route_table", "python:app.main:app").value["complete"] is True
    assert "python:app.main:app#GET:/v1/late" in _all(r, "fastapi.route")


def test_settings_inheritance_annotated_alias_and_custom_sources(tmp_path: Path) -> None:
    files = {"app/settings.py": """
        from typing import Annotated
        from pydantic import Field
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class Base(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="APP_")

        class Settings(Base):
            deadline: float = 5.0
            timeout: Annotated[float, Field(validation_alias="REQ_TIMEOUT")] = 2.0

        class Custom(BaseSettings):
            x: int = 1

            @classmethod
            def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
                return (init_settings,)
        """}
    r = _repo(tmp_path, files, command=["uvicorn", "app.main:app"])
    s = _all(r, "python.settings_field")
    assert s["python:app.settings.Settings#deadline"].value["env"] == "APP_DEADLINE"
    assert s["python:app.settings.Settings#timeout"].value["env"] == "REQ_TIMEOUT"
    assert any("customised sources" in c.message or "env file" in c.message for c in r.coverage)


def test_one_deadline_per_block_and_the_innermost_applies(tmp_path: Path) -> None:
    files = dict(HANDLERS)
    files["app/routes.py"] = """
        import asyncio
        from fastapi import APIRouter
        from app import client

        router = APIRouter()

        @router.get("/a")
        async def a():
            async with asyncio.timeout(1):
                pass
            async with asyncio.timeout(20):
                return await client.fetch()
        """
    r = _repo(tmp_path, files, command=["uvicorn", "app.main:app"])
    d = [f for f in r.facts if f.kind == "python.deadline"]
    assert sorted(f.value["seconds"] for f in d) == [1.0, 20.0]
    call = _f(r, "python.call_reaches", "python:app.routes.a")
    twenty = next(f for f in d if f.value["seconds"] == 20.0)
    assert any(i.fact_id == twenty.fact_id for i in call.inputs)


def test_client_timeout_reassignment_and_per_request_timeout(tmp_path: Path) -> None:
    files = {"app/r.py": "import httpx\nfrom tenacity import retry\nc = httpx.Client()\nc.timeout = httpx.Timeout(60.0)\n"
                         "d = httpx.Client(timeout=2)\n@retry\ndef f():\n    return c.get('/')\n@retry\ndef g():\n    return d.get('/', timeout=30.0)\n"}
    r = _repo(tmp_path, files)
    assert _f(r, "python.http_client.timeout", "python:app.r.c").value["read"] == 60.0
    g = _f(r, "python.retry.client", "python:app.r.g")
    assert g.value["request_timeout"]["read"] == 30.0
