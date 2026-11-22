"""Reference service for the FifthNine standard.

One endpoint, GET /quote, fetches a price from an upstream service and returns it.
The service also serves /healthz (liveness), /readyz (readiness) and /metrics (Prometheus).
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

MAX_ATTEMPTS_CEILING = 5

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Time spent serving HTTP requests.",
    ["route", "method"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0),
)
REQUESTS = Counter("http_requests_total", "HTTP requests served.", ["route", "method", "code"])
REQUEST_ERRORS = Counter("http_request_errors_total", "HTTP requests that ended in a 5xx response.", ["route", "method"])
IN_FLIGHT = Gauge("http_requests_in_flight", "Requests being served now.")
IN_FLIGHT_LIMIT = Gauge("http_requests_in_flight_limit", "Maximum requests served at once.")
UPSTREAM_DURATION = Histogram("upstream_request_duration_seconds", "Time spent on each upstream attempt.", ["outcome"])


@dataclass(frozen=True)
class Config:
    port: int
    log_level: str
    upstream_url: str
    upstream_token: str
    upstream_timeout_seconds: float
    upstream_max_attempts: int
    max_in_flight: int

    @staticmethod
    def from_env(env: Mapping[str, str]) -> Config:
        attempts = int(env.get("UPSTREAM_MAX_ATTEMPTS", "3"))
        if not 1 <= attempts <= MAX_ATTEMPTS_CEILING:
            raise ValueError(f"UPSTREAM_MAX_ATTEMPTS must be between 1 and {MAX_ATTEMPTS_CEILING}")
        timeout = float(env.get("UPSTREAM_TIMEOUT_SECONDS", "2"))
        if timeout <= 0:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS must be greater than zero")
        return Config(
            port=int(env.get("PORT", "8080")),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            upstream_url=env["UPSTREAM_URL"],
            upstream_token=env["UPSTREAM_TOKEN"],
            upstream_timeout_seconds=timeout,
            upstream_max_attempts=attempts,
            max_in_flight=int(env.get("MAX_IN_FLIGHT", "64")),
        )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        return json.dumps(entry)


log = logging.getLogger("reference-service")


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.handlers = [handler]
    log.setLevel(level)
    log.propagate = False


class UpstreamError(Exception):
    pass


Opener = Callable[..., object]


def call_upstream(
    cfg: Config,
    request_id: str,
    opener: Opener = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Call the upstream with a timeout and a capped number of attempts.

    The timeout applies to the connect and to every blocking read on the socket.
    Retries use exponential backoff with full jitter. 4xx responses are not retried.
    """
    for attempt in range(1, cfg.upstream_max_attempts + 1):
        request = urllib.request.Request(  # noqa: S310 - URL comes from deployment config
            cfg.upstream_url,
            headers={"Authorization": f"Bearer {cfg.upstream_token}", "X-Request-ID": request_id},
        )
        started = time.perf_counter()
        try:
            with opener(request, timeout=cfg.upstream_timeout_seconds) as response:  # type: ignore[attr-defined]
                body = json.loads(response.read())
            UPSTREAM_DURATION.labels("ok").observe(time.perf_counter() - started)
            return body
        except urllib.error.HTTPError as exc:
            UPSTREAM_DURATION.labels("error").observe(time.perf_counter() - started)
            if exc.code < 500:
                raise UpstreamError(f"upstream returned {exc.code}") from exc
            failure: Exception = exc
        except (OSError, ValueError) as exc:
            UPSTREAM_DURATION.labels("error").observe(time.perf_counter() - started)
            failure = exc
        log.warning("upstream attempt %d of %d failed: %s", attempt, cfg.upstream_max_attempts, failure, extra={"request_id": request_id})
        if attempt < cfg.upstream_max_attempts:
            sleep(random.uniform(0, min(1.0, 0.1 * 2 ** (attempt - 1))))  # noqa: S311 - jitter, not security
    raise UpstreamError("upstream unavailable after retries")


class Service:
    def __init__(self, cfg: Config, opener: Opener = urllib.request.urlopen) -> None:
        self.cfg = cfg
        self.opener = opener
        self.ready = threading.Event()
        self.slots = threading.BoundedSemaphore(cfg.max_in_flight)
        IN_FLIGHT_LIMIT.set(cfg.max_in_flight)

    def handler(self) -> type[BaseHTTPRequestHandler]:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature set by the base class
                return

            def send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - name set by the base class
                route = self.path.split("?", 1)[0]
                if route == "/healthz":
                    self.send(200, b'{"status":"ok"}')
                    return
                if route == "/readyz":
                    ok = service.ready.is_set()
                    self.send(200 if ok else 503, b'{"status":"ready"}' if ok else b'{"status":"not ready"}')
                    return
                if route == "/metrics":
                    self.send(200, generate_latest(), CONTENT_TYPE_LATEST)
                    return
                if route != "/quote":
                    self.send(404, b'{"error":"not found"}')
                    return
                self.serve_quote()

            def serve_quote(self) -> None:
                request_id = self.headers.get("X-Request-ID") or str(uuid.uuid4())
                started = time.perf_counter()
                code = 200
                if not service.slots.acquire(blocking=False):
                    code = 503
                    self.send(code, b'{"error":"overloaded"}')
                else:
                    IN_FLIGHT.inc()
                    try:
                        quote = call_upstream(service.cfg, request_id, service.opener)
                        self.send(code, json.dumps({"quote": quote, "request_id": request_id}).encode())
                    except UpstreamError as exc:
                        code = 502
                        log.error("quote failed: %s", exc, extra={"request_id": request_id})
                        self.send(code, b'{"error":"upstream unavailable"}')
                    finally:
                        IN_FLIGHT.dec()
                        service.slots.release()
                REQUEST_DURATION.labels("/quote", "GET").observe(time.perf_counter() - started)
                REQUESTS.labels("/quote", "GET", str(code)).inc()
                if code >= 500:
                    REQUEST_ERRORS.labels("/quote", "GET").inc()
                log.info("GET /quote %d", code, extra={"request_id": request_id})

        return Handler


def main() -> None:
    cfg = Config.from_env(os.environ)
    configure_logging(cfg.log_level)
    service = Service(cfg)
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), service.handler())  # noqa: S104 - container listens on all interfaces

    def stop(signum: int, frame: object) -> None:
        log.info("shutting down")
        service.ready.clear()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    service.ready.set()
    log.info("listening on port %d", cfg.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
