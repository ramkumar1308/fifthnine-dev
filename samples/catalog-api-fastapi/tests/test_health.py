from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
