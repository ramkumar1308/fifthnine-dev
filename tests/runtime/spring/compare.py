"""M2-D3: compare M2 Spring facts with what a running Spring Boot application actually resolved.

Pure logic, no network and no Java: `runtime_keys` lists the properties to ask the application about, `endpoint_probes` lists the
actuator URLs the facts promise, and `compare` matches facts against the application's dump and the HTTP results. It is unit-tested
with synthetic dumps (tests/unit/test_runtime_compare.py) and driven by run_cases.py in the separate runtime workflow.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from fifthnine.schema.v1 import Fact, FactStatus

PROPERTY = "spring.property"
ENDPOINT = "spring.actuator.endpoint"
PROFILES = "spring.active_profiles"
PROBE_GROUPS = "spring.actuator.probe_groups"

_DUR = re.compile(r"^([+-]?\d+)\s*(ns|us|ms|s|m|h|d)?$", re.IGNORECASE)
_ISO = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", re.IGNORECASE)
_MS = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1000.0, "m": 60000.0, "h": 3600000.0, "d": 86400000.0}
_TRUE, _FALSE = {"true", "on", "yes", "1"}, {"false", "off", "no", "0"}

# Origin text Spring reports for each layer (ConfigurationProperty.getOrigin()), checked as substrings.
ORIGIN_MARKERS = {
    "spring.command_line": ("commandLineArgs",),
    "spring.application_json": ("spring.application.json", "SPRING_APPLICATION_JSON"),
    "spring.system_property": ("System Property",),
    "spring.os_environment": ("System Environment Property",),
}


@dataclass
class Check:
    case: str
    kind: str
    subject: str
    ok: bool
    expected: Any = None
    runtime: Any = None
    note: str = ""


@dataclass
class CaseResult:
    case: str
    checks: list[Check] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def key_of(f: Fact) -> str:
    return f.subject.key.split(":", 2)[2]


def runtime_keys(facts: list[Fact]) -> list[str]:
    return sorted({key_of(f) for f in facts if f.kind == PROPERTY})


def endpoint_probes(facts: list[Fact]) -> list[tuple[int, str]]:
    """(port, path) of every present endpoint the facts promise, including additional paths, plus the discovery page."""
    out: set[tuple[int, str]] = set()
    for f in facts:
        if f.kind == ENDPOINT and isinstance(f.value, dict) and f.status is not FactStatus.OVERRIDDEN:
            out.add((int(f.value["port"]), str(f.value["path"])))
            extra = f.value.get("additional_path")
            if isinstance(extra, dict):
                out.add((int(extra["port"]), str(extra["path"])))
    return sorted(out)


def absent_probes(facts: list[Fact], server_port: int | None) -> list[tuple[int, str]]:
    """Where an endpoint is promised to be absent, the default URL on the port the application really listens on."""
    if server_port is None:
        return []
    return [(server_port, "/actuator/health/" + f.subject.key.rsplit("/", 1)[1]) for f in facts if f.kind == ENDPOINT and f.absent]


def _duration_ms(text: str) -> float | None:
    t = text.strip()
    m = _DUR.match(t)
    if m:
        return int(m.group(1)) * _MS[(m.group(2) or "ms").lower()]
    iso = _ISO.match(t)
    if iso and any(iso.groups()):
        h, mi, s = (float(g or 0) for g in iso.groups())
        return (h * 3600 + mi * 60 + s) * 1000
    return None


def same_value(f: Fact, runtime: str | None, as_list: list[str] | None) -> bool:
    v = f.value
    if isinstance(v, list):
        got = as_list if as_list is not None else ([p.strip() for p in runtime.split(",") if p.strip()] if runtime is not None else None)
        return got is not None and [str(x) for x in v] == got
    if runtime is None:
        return v in (None, "")
    if isinstance(v, bool):
        word = runtime.strip().lower()
        return (word in _TRUE and v) or (word in _FALSE and not v)
    if f.unit and isinstance(v, (int, float)):
        want, got_ms = v * _MS.get(f.unit, 1.0), _duration_ms(runtime)
        return got_ms is not None and abs(want - got_ms) < 1e-6
    if isinstance(v, (int, float)):
        return runtime.strip() == str(v)
    return runtime == str(v)


def origin_ok(f: Fact, origin: str | None) -> tuple[bool, str]:
    """Whether Spring's reported origin of the property agrees with the layer, file and line the fact cites."""
    if origin is None:
        return True, "no origin reported"
    prec = f.precedence or ""
    if prec.startswith("spring.packaged_"):
        if f.source is None:
            return False, "fact has no source"
        name = f.source.path.rsplit("src/main/resources/", 1)[-1]
        m = re.search(r"\[([^\]]+)\](?: from \S+)?\s*-\s*(\d+):", origin)
        if m is None:
            return False, "origin is not a classpath resource"
        return (m.group(1) == name and int(m.group(2)) == f.source.line_start), f"origin {m.group(1)}:{m.group(2)}"
    marks = ORIGIN_MARKERS.get(prec)
    if marks is None:
        return True, f"no origin rule for {prec}"
    return any(m in origin for m in marks), "origin layer"


def _bound(key: str, rt: dict[str, Any]) -> tuple[bool, Any]:
    b = rt.get("bound", {})
    if key == "server.port":
        return "serverPort" in rt, rt.get("serverPort")
    if key == "spring.lifecycle.timeout-per-shutdown-phase":
        return "spring.lifecycle.timeout-per-shutdown-phase.ms" in b, b.get("spring.lifecycle.timeout-per-shutdown-phase.ms")
    if key == "management.endpoints.web.path-mapping.health":
        return "management.endpoints.web.path-mapping" in b, b.get("management.endpoints.web.path-mapping", {}).get("health")
    if key == "management.endpoint.health.probes.add-additional-paths":
        return True, False  # Boot reads it with equalsIgnoreCase("true"); no bean exposes it
    if key == "management.endpoints.web.exposure.include":
        return False, None  # observed through the discovery page instead
    return key in b, b.get(key)


def compare(case: str, facts: list[Fact], rt: dict[str, Any], http: dict[tuple[int, str], int | None], discovery: list[str] | None = None) -> CaseResult:
    res = CaseResult(case)
    add = res.checks.append
    values = rt.get("values", {})
    for f in facts:
        if f.status in (FactStatus.OVERRIDDEN, FactStatus.UNRESOLVED) and f.kind != ENDPOINT:
            res.skipped[f"{f.kind} {f.status.value}"] += 1
            continue
        if f.kind == PROPERTY:
            _property(case, f, rt, values, res, discovery)
        elif f.kind == PROFILES:
            _profiles(case, f, rt, res)
        elif f.kind == PROBE_GROUPS:
            got = rt.get("healthGroups")
            if got is None:  # no HealthEndpointGroups bean: the health endpoint is disabled or not exposed, so there is nothing to observe
                res.skipped["probe groups not observable (health endpoint absent)"] += 1
            elif isinstance(f.value, dict):
                want = sorted(g for g, on in f.value.items() if on)
                add(Check(case, f.kind, f.subject.key, got is not None and sorted(got) == want, want, got))
        elif f.kind == ENDPOINT:
            _endpoint(case, f, rt, http, res)
    return res


def _property(case: str, f: Fact, rt: dict[str, Any], values: dict[str, Any], res: CaseResult, discovery: list[str] | None) -> None:
    key = key_of(f)
    entry = values.get(key, {})
    text, listed = entry.get("value"), entry.get("list")
    if f.value == "<redacted>":
        res.skipped["redacted value"] += 1
        return
    if f.status is FactStatus.DEFAULT:
        if text is not None or listed is not None:
            res.checks.append(Check(case, f.kind, f.subject.key, False, f"unset (default {f.value!r})", text if text is not None else listed,
                                    "the property is set at runtime, but the scan reports the default"))
        if key == "management.endpoints.web.exposure.include":
            if discovery is None:
                res.skipped["default exposure not observed"] += 1
            else:
                res.checks.append(Check(case, f.kind, f.subject.key, sorted(discovery) == sorted(f.value or []), f.value, discovery, "discovery page"))
            return
        ok, got = _bound(key, rt)
        if not ok:
            res.skipped[f"default of {key} not observable"] += 1
            return
        want = f.value
        if f.unit and want is not None:
            want = want * _MS.get(f.unit, 1.0)
        if isinstance(got, list):
            got = sorted(got)
            want = sorted(want or [])
        res.checks.append(Check(case, f.kind, f.subject.key, (want in (None, "") and got in (None, "")) or want == got or str(want) == str(got), f.value, got))
        return
    res.checks.append(Check(case, f.kind, f.subject.key, same_value(f, text, listed), f.value, text if text is not None else listed))
    ok, note = origin_ok(f, entry.get("origin"))
    if not ok:
        res.checks.append(Check(case, "origin:" + f.kind, f.subject.key, False, f"{f.precedence} {f.source.path if f.source else ''}:"
                                f"{f.source.line_start if f.source else ''}", entry.get("origin"), note))
    elif entry.get("origin") is not None:
        res.checks.append(Check(case, "origin:" + f.kind, f.subject.key, True, f.precedence, entry.get("origin")))


def _profiles(case: str, f: Fact, rt: dict[str, Any], res: CaseResult) -> None:
    want = list(f.value or [])
    active, default = list(rt.get("activeProfiles", [])), list(rt.get("defaultProfiles", []))
    if not want:  # nothing is activated: Boot accepts the profile named "default"
        ok = not active and default == ["default"]
    elif f.precedence == "spring.default_profile" or (not active and default):
        # spring.profiles.default named other profiles (Environment reports the names before group expansion)
        ok = not active and bool(default) and set(default) <= set(want)
    else:
        ok = sorted(active) == sorted(want)
    res.checks.append(Check(case, f.kind, f.subject.key, ok, f.value, {"active": active, "default": default}))


def _endpoint(case: str, f: Fact, rt: dict[str, Any], http: dict[tuple[int, str], int | None], res: CaseResult) -> None:
    if f.status is FactStatus.UNRESOLVED:
        res.skipped["endpoint unresolved"] += 1
        return
    if f.absent:
        got = http.get((int(rt.get("serverPort") or 0), "/actuator/health/" + f.subject.key.rsplit("/", 1)[1]))
        res.checks.append(Check(case, f.kind, f.subject.key, got != 200, "absent", got, "HTTP status of the default URL on the main port"))
        return
    assert isinstance(f.value, dict)
    port, path = int(f.value["port"]), str(f.value["path"])
    got = http.get((port, path))
    res.checks.append(Check(case, f.kind, f.subject.key, got == 200, f"200 at :{port}{path}", got))
    res.checks.append(Check(case, f.kind + ":port_type", f.subject.key, rt.get("portType") == f.value["port_type"], f.value["port_type"], rt.get("portType")))
    extra = f.value.get("additional_path")
    if isinstance(extra, dict):
        key = (int(extra["port"]), str(extra["path"]))
        res.checks.append(Check(case, f.kind + ":additional_path", f.subject.key, http.get(key) == 200, f"200 at :{key[0]}{key[1]}", http.get(key)))
