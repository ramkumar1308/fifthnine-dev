"""Spring property vocabulary shared by the M2-2 resolver: names, typed conversion, placeholders, redaction.

Everything here is pure text handling. Nothing is evaluated: placeholders are resolved by a small parser, durations
by `DurationStyle`'s simple and ISO-8601 forms, and no value is ever executed or interpolated into a shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_DEPTH = 10
MAX_PLACEHOLDER_TEXT = 4096

_INDEXED = re.compile(r"^(.*)\[(\d+)\]$")
_SIMPLE_DURATION = re.compile(r"^([+-]?\d+)\s*(ns|us|ms|s|m|h|d)?$", re.IGNORECASE)
_ISO_DURATION = re.compile(r"^([+-])?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$", re.IGNORECASE)
_INT = re.compile(r"^[+-]?\d+$")
DURATION_NAME = re.compile(r"(timeout|duration|interval|delay|wait|period|ttl|age|expir|backoff|grace|idle|lifetime|keep-?alive)", re.IGNORECASE)
SECRET_NAME = re.compile(r"(password|passwd|secret|token|credential|api-?key|private-?key|passphrase|access-?key)", re.IGNORECASE)
_URL_USERINFO = re.compile(r"(//)[^/@\s]+@")
_QUERY_SECRET = re.compile(r"((?:password|pwd|secret|token|api-?key)=)[^&;\s]+", re.IGNORECASE)

REDACTED = "<redacted>"

# Keys the resolver reports. A key is relevant when it starts with one of these prefixes, or when its last segment
# names an address, a port, a timeout or a retry bound (anything an SRE check could ask about).
RELEVANT_PREFIXES = (
    "server.", "management.", "spring.lifecycle.", "spring.http.", "spring.datasource.", "spring.data.", "spring.redis.",
    "spring.kafka.", "spring.rabbitmq.", "spring.cloud.", "spring.task.", "spring.mvc.", "spring.webflux.", "spring.jpa.",
    "spring.cache.", "spring.session.", "spring.threads.", "spring.application.name", "spring.main.cloud-platform",
    "spring.mail.", "spring.elasticsearch.", "spring.mongodb.", "spring.neo4j.", "spring.flyway.", "spring.liquibase.",
    "resilience4j.", "springdoc.", "feign.", "okhttp.",
)
_RELEVANT_LAST = re.compile(
    r"(base-?url|url|uri|uris|host|hostname|port|timeout|timeouts|max-attempts|retries|attempts|ttl|endpoint|endpoints)$", re.IGNORECASE)
HANDLED_ELSEWHERE = ("spring.profiles.",)

# Registry: keys whose type the resolver knows. Everything else is typed from its value.
SPECS: dict[str, str] = {
    "server.port": "int",
    "management.server.port": "int",
    "server.shutdown": "str",
    "server.servlet.context-path": "str",
    "server.error.include-stacktrace": "str",
    "spring.lifecycle.timeout-per-shutdown-phase": "duration",
    "management.server.base-path": "str",
    "management.endpoints.web.base-path": "str",
    "management.endpoints.web.exposure.include": "list",
    "management.endpoints.web.exposure.exclude": "list",
    "management.endpoint.health.probes.enabled": "bool",
    "management.endpoint.health.probes.add-additional-paths": "bool",
    "management.endpoints.access.default": "str",
    "management.endpoints.access.max-permitted": "str",
    "management.endpoints.enabled-by-default": "bool",
    "spring.main.cloud-platform": "str",
    "management.endpoint.health.show-details": "str",
}
SPEC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^management\.endpoints\.web\.path-mapping\.[^.]+$"), "str"),
    (re.compile(r"^management\.endpoint\.[^.]+\.access$"), "str"),
    (re.compile(r"^management\.endpoint\.[^.]+\.enabled$"), "bool"),
)


def canon(key: str) -> str:
    """Canonical form used to compare property names: lower case, no dashes or underscores outside `[n]` indexes."""
    if "[" not in key:
        return key.lower().replace("-", "").replace("_", "")
    parts = re.split(r"(\[\d+\])", key)
    return "".join(p if p.startswith("[") else p.lower().replace("-", "").replace("_", "") for p in parts)


def env_name(key: str) -> str:
    """Environment variable that binds `key` under Spring Boot's relaxed binding: dots to underscores, dashes removed,
    upper case, list indexes as `_0_`."""
    spaced = re.sub(r"\[(\d+)\]", r".\1.", key)
    parts = [p.replace("-", "") for p in spaced.split(".") if p]
    return "_".join(parts).upper()


def split_index(key: str) -> tuple[str, int | None]:
    m = _INDEXED.match(key)
    return (m.group(1), int(m.group(2))) if m else (key, None)


def spec_type(key: str) -> str | None:
    if key in SPECS:
        return SPECS[key]
    for pattern, kind in SPEC_PATTERNS:
        if pattern.match(key):
            return kind
    return None


def is_relevant(key: str) -> bool:
    if any(key.startswith(p) for p in HANDLED_ELSEWHERE):
        return False
    if key in SPECS or any(key.startswith(p) for p in RELEVANT_PREFIXES):
        return True
    last = key.rsplit(".", 1)[-1]
    return bool(_RELEVANT_LAST.search(last))


def redact(key: str, value: Any) -> Any:
    """Secret-looking values never reach the report: the key name decides, then URL credentials are dropped."""
    if isinstance(value, list):
        return [redact(key, v) for v in value]
    if not isinstance(value, str):
        return REDACTED if SECRET_NAME.search(key.rsplit(".", 1)[-1]) and value not in ("", None, True, False) else value
    if value and SECRET_NAME.search(key.rsplit(".", 1)[-1]) and "${" not in value:
        return REDACTED
    return _QUERY_SECRET.sub(r"\1" + REDACTED, _URL_USERINFO.sub(r"\1" + REDACTED + "@", value))


# ---------------------------------------------------------------- typed values


def parse_duration(text: str, default_unit: str = "ms") -> tuple[int | float, str] | None:
    """Spring `DurationStyle`: `45s`, `500ms`, a bare number in `default_unit`, or ISO-8601 (`PT45S`). The unit written
    is kept; ISO-8601 is reported in seconds when whole, else milliseconds."""
    t = text.strip()
    m = _SIMPLE_DURATION.match(t)
    if m:
        return int(m.group(1)), (m.group(2) or default_unit).lower()
    iso = _ISO_DURATION.match(t)
    if iso and any(iso.group(i) for i in (2, 3, 4, 5)):
        days, hours, minutes, seconds = (float(iso.group(i) or 0) for i in (2, 3, 4, 5))
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        total = -total if iso.group(1) == "-" else total
        if total == int(total):
            return int(total), "s"
        return int(round(total * 1000)), "ms"
    return None


def _scalar_text(raw: Any) -> str:
    if isinstance(raw, bool):
        return "true" if raw else "false"
    return str(raw)


def convert(key: str, raw: Any, kind: str | None = None) -> tuple[Any, str | None]:
    """Typed value and unit of a raw configuration value. `kind` is the registry type; without it the value is typed
    from its own form (durations only when the key names a time setting)."""
    kind = kind or spec_type(key)
    if isinstance(raw, list):
        return [(_scalar_text(v) if kind in ("list", "str", None) else convert(key, v, kind)[0]) for v in raw], None
    if kind == "list":
        text = _scalar_text(raw)
        return [p.strip() for p in text.split(",") if p.strip()], None
    if kind == "duration" or (kind is None and isinstance(raw, str) and DURATION_NAME.search(key.rsplit(".", 1)[-1])
                              and _SIMPLE_DURATION.match(raw.strip()) and not _INT.match(raw.strip())):
        parsed = parse_duration(_scalar_text(raw))
        if parsed is not None:
            return parsed
        return _scalar_text(raw), None
    if kind == "int":
        text = _scalar_text(raw).strip()
        return (int(text), None) if _INT.match(text) else (_scalar_text(raw), None)
    if kind == "bool":
        text = _scalar_text(raw).strip().lower()
        return (text == "true", None) if text in ("true", "false") else (_scalar_text(raw), None)
    if kind == "str":
        return _scalar_text(raw), None
    if isinstance(raw, str):
        text = raw.strip()
        if _INT.match(text) and len(text) < 16:
            return int(text), None
        if text.lower() in ("true", "false"):
            return text.lower() == "true", None
    return raw, None


# ---------------------------------------------------------------- placeholders


@dataclass
class PlaceholderUse:
    name: str
    default: str | None
    start: int
    end: int


def find_placeholders(text: str) -> list[PlaceholderUse] | None:
    """Top-level `${name:default}` expressions of `text`, or None when the braces do not balance."""
    out: list[PlaceholderUse] = []
    i = 0
    while i < len(text):
        if text.startswith("${", i):
            depth = 1
            j = i + 2
            while j < len(text) and depth:
                if text.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if text[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                return None
            inner = text[i + 2:j - 1]
            name, default = _split_default(inner)
            out.append(PlaceholderUse(name, default, i, j))
            i = j
        else:
            i += 1
    return out


def _split_default(inner: str) -> tuple[str, str | None]:
    depth = 0
    i = 0
    while i < len(inner):
        if inner.startswith("${", i):
            depth += 1
            i += 2
            continue
        if inner[i] == "}" and depth:
            depth -= 1
        elif inner[i] == ":" and depth == 0:
            return inner[:i], inner[i + 1:]
        i += 1
    return inner, None
