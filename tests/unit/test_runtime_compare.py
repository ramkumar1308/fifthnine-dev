"""M2-D3 comparison logic, tested with synthetic runtime dumps (no JVM). The real runs happen in the spring-runtime workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fifthnine.core.engine import ScanOptions, run_scan
from fifthnine.schema.v1 import Fact
from tests.runtime.spring import compare as C
from tests.runtime.spring.cases import CASES

from .test_m2_2_spring import Repo

YML = (
    "server:\n  port: 8081\nspring:\n  lifecycle:\n    timeout-per-shutdown-phase: 45s\n"
    "management:\n  endpoints:\n    web:\n      exposure:\n        include: health,info\n"
)


def _facts(tmp: Path, **kw: Any) -> list[Fact]:
    r = run_scan(ScanOptions(path=Repo(resources={"application.yml": YML}, **kw).write(tmp), facts="all"))
    return [f for f in r.facts if f.kind.startswith("spring.")]


def _runtime(**over: Any) -> dict[str, Any]:
    rt: dict[str, Any] = {
        "activeProfiles": [], "defaultProfiles": ["default"], "serverPort": 8081, "portType": "SAME", "healthGroups": ["liveness", "readiness"],
        "bound": {"server.shutdown": "graceful", "server.servlet.context-path": None, "spring.lifecycle.timeout-per-shutdown-phase.ms": 30000,
                  "management.server.port": None, "management.server.base-path": "", "management.endpoints.web.base-path": "/actuator",
                  "management.endpoints.web.path-mapping": {}, "management.endpoints.web.exposure.exclude": []},
        "values": {
            "server.port": {"value": "8081", "origin": "class path resource [application.yml] - 2:9"},
            "spring.lifecycle.timeout-per-shutdown-phase": {"value": "45s", "origin": "class path resource [application.yml] - 5:35"},
            "management.endpoints.web.exposure.include": {"value": "health,info", "origin": "class path resource [application.yml] - 10:18"},
        },
    }
    rt.update(over)
    return rt


HTTP_OK = {(8081, "/actuator/health/liveness"): 200, (8081, "/actuator/health/readiness"): 200}


def test_matching_runtime_passes(tmp_path: Path) -> None:
    res = C.compare("t", _facts(tmp_path), _runtime(), HTTP_OK)
    assert not res.failures, [c.__dict__ for c in res.failures]
    assert any(c.kind == "spring.actuator.endpoint" for c in res.checks)


def test_wrong_value_origin_line_and_http_status_are_failures(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    bad_value = _runtime()
    bad_value["values"]["server.port"]["value"] = "9090"
    assert any(c.subject.endswith("server.port") for c in C.compare("t", facts, bad_value, HTTP_OK).failures)
    bad_line = _runtime()
    bad_line["values"]["server.port"]["origin"] = "class path resource [application.yml] - 7:9"
    assert any(c.kind.startswith("origin:") for c in C.compare("t", facts, bad_line, HTTP_OK).failures)
    bad_layer = _runtime()
    bad_layer["values"]["server.port"]["origin"] = 'System Environment Property "SERVER_PORT"'
    assert any(c.kind.startswith("origin:") for c in C.compare("t", facts, bad_layer, HTTP_OK).failures)
    down = {**HTTP_OK, (8081, "/actuator/health/liveness"): 404}
    assert any(c.kind == "spring.actuator.endpoint" for c in C.compare("t", facts, _runtime(), down).failures)


def test_a_default_that_is_set_at_runtime_or_differs_is_a_failure(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    leaked = _runtime()
    leaked["values"]["server.shutdown"] = {"value": "immediate"}
    assert any(c.subject.endswith("server.shutdown") for c in C.compare("t", facts, leaked, HTTP_OK).failures)
    other = _runtime()
    other["bound"]["server.shutdown"] = "immediate"
    assert any(c.subject.endswith("server.shutdown") for c in C.compare("t", facts, other, HTTP_OK).failures)
    ok = C.compare("t", facts, _runtime(), HTTP_OK)
    assert any(c.subject.endswith("server.shutdown") and c.ok for c in ok.checks)


def test_profiles_and_probe_groups_are_compared(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    assert any(c.kind == "spring.active_profiles" for c in C.compare("t", facts, _runtime(), HTTP_OK).checks)
    assert any(c.kind == "spring.active_profiles" for c in C.compare("t", facts, _runtime(activeProfiles=["prod"]), HTTP_OK).failures)
    assert any(c.kind == "spring.actuator.probe_groups" for c in C.compare("t", facts, _runtime(healthGroups=["liveness"]), HTTP_OK).failures)


def test_duration_boolean_and_list_normalisation() -> None:
    assert C._duration_ms("PT1M") == 60000 and C._duration_ms("45s") == 45000 and C._duration_ms("30") == 30
    assert C._duration_ms("nonsense") is None


def test_case_names_are_unique_and_entrypoints_name_the_jar() -> None:
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
    assert all(c.entrypoint is None or "/app/app.jar" in c.entrypoint for c in CASES)
    assert all(c.resources for c in CASES)
