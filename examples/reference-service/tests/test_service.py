import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service  # noqa: E402

ENV = {"UPSTREAM_URL": "http://upstream.invalid/price", "UPSTREAM_TOKEN": "test-token"}


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    """Returns or raises the queued outcomes in order and records each call."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(json.dumps(outcome).encode())


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://upstream.invalid/price", code, "error", {}, None)  # type: ignore[arg-type]


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = service.Config.from_env(ENV)
        self.assertEqual(cfg.port, 8080)
        self.assertEqual(cfg.upstream_max_attempts, 3)
        self.assertEqual(cfg.upstream_timeout_seconds, 2.0)

    def test_rejects_unbounded_retries(self) -> None:
        for value in ("0", "6"):
            with self.assertRaises(ValueError):
                service.Config.from_env({**ENV, "UPSTREAM_MAX_ATTEMPTS": value})

    def test_rejects_missing_timeout(self) -> None:
        with self.assertRaises(ValueError):
            service.Config.from_env({**ENV, "UPSTREAM_TIMEOUT_SECONDS": "0"})

    def test_requires_upstream_token(self) -> None:
        with self.assertRaises(KeyError):
            service.Config.from_env({"UPSTREAM_URL": ENV["UPSTREAM_URL"]})


class UpstreamTests(unittest.TestCase):
    cfg = service.Config.from_env({**ENV, "UPSTREAM_TIMEOUT_SECONDS": "1.5"})

    def test_passes_timeout_on_every_attempt(self) -> None:
        opener = FakeOpener(OSError("reset"), {"price": 7})
        self.assertEqual(service.call_upstream(self.cfg, "r1", opener, sleep=lambda s: None), {"price": 7})
        self.assertEqual(opener.timeouts, [1.5, 1.5])

    def test_stops_after_max_attempts(self) -> None:
        opener = FakeOpener(TimeoutError(), TimeoutError(), TimeoutError(), {"price": 1})
        with self.assertRaises(service.UpstreamError):
            service.call_upstream(self.cfg, "r2", opener, sleep=lambda s: None)
        self.assertEqual(len(opener.timeouts), 3)

    def test_retries_5xx_but_not_4xx(self) -> None:
        opener = FakeOpener(http_error(503), {"price": 2})
        self.assertEqual(service.call_upstream(self.cfg, "r3", opener, sleep=lambda s: None), {"price": 2})
        opener = FakeOpener(http_error(404), {"price": 3})
        with self.assertRaises(service.UpstreamError):
            service.call_upstream(self.cfg, "r4", opener, sleep=lambda s: None)
        self.assertEqual(len(opener.timeouts), 1)

    def test_backoff_is_bounded(self) -> None:
        waits: list[float] = []
        opener = FakeOpener(OSError(), OSError(), OSError())
        with self.assertRaises(service.UpstreamError):
            service.call_upstream(self.cfg, "r5", opener, sleep=waits.append)
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(0 <= w <= 1.0 for w in waits))


class ServerTests(unittest.TestCase):
    def start(self, opener: FakeOpener, max_in_flight: int = 4) -> tuple[service.Service, str]:
        cfg = service.Config.from_env({**ENV, "MAX_IN_FLIGHT": str(max_in_flight)})
        svc = service.Service(cfg, opener)
        server = ThreadingHTTPServer(("127.0.0.1", 0), svc.handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return svc, f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, url: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - local test server
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_readiness_follows_state_and_liveness_is_always_up(self) -> None:
        svc, base = self.start(FakeOpener())
        self.assertEqual(self.get(base + "/healthz")[0], 200)
        self.assertEqual(self.get(base + "/readyz")[0], 503)
        svc.ready.set()
        self.assertEqual(self.get(base + "/readyz")[0], 200)

    def test_quote_and_metrics(self) -> None:
        _, base = self.start(FakeOpener({"price": 42}))
        status, body = self.get(base + "/quote")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["quote"], {"price": 42})
        status, metrics = self.get(base + "/metrics")
        self.assertEqual(status, 200)
        for name in (b"http_request_duration_seconds", b"http_requests_total", b"http_request_errors_total", b"http_requests_in_flight"):
            self.assertIn(name, metrics)

    def test_upstream_failure_returns_502(self) -> None:
        _, base = self.start(FakeOpener(http_error(400)))
        self.assertEqual(self.get(base + "/quote")[0], 502)

    def test_sheds_load_when_saturated(self) -> None:
        svc, base = self.start(FakeOpener({"price": 1}), max_in_flight=1)
        svc.slots.acquire()
        try:
            self.assertEqual(self.get(base + "/quote")[0], 503)
        finally:
            svc.slots.release()

    def test_unknown_route_is_404(self) -> None:
        _, base = self.start(FakeOpener())
        self.assertEqual(self.get(base + "/nope")[0], 404)


class LoggingTests(unittest.TestCase):
    def test_log_lines_are_json_with_request_id(self) -> None:
        import logging

        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        record.request_id = "abc"
        line = json.loads(service.JsonFormatter().format(record))
        self.assertEqual(line["message"], "hello world")
        self.assertEqual(line["request_id"], "abc")


if __name__ == "__main__":
    unittest.main()
