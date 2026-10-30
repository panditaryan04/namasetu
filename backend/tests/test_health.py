from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings


def test_health_reports_local_modes(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["modes"]["DB_MODE"] == "sqlite-local"
    assert body["modes"]["TASK_MODE"] == "eager-local"
    assert body["modes"]["ICD11_MODE"] == "mock"
    assert body["modes"]["ABHA_MODE"] == "mock"
    assert body["modes"]["DATA_MODE"] == "sample"
    assert "Not a certified medical device" in body["disclaimer"]


def test_request_id_and_security_headers(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]


def test_generated_request_id_when_missing(client: TestClient) -> None:
    resp = client.get("/health")
    assert len(resp.headers["X-Request-ID"]) == 32


def test_error_envelope_on_404(client: TestClient) -> None:
    resp = client.get("/does-not-exist", headers={"X-Request-ID": "req-404"})
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "http_404"
    assert err["request_id"] == "req-404"


def test_icd11_mode_auto_requires_both_keys() -> None:
    assert Settings(icd_client_id="x").effective_icd11_mode == "mock"
    assert Settings(icd_client_id="x", icd_client_secret="y").effective_icd11_mode == "live"
    assert (
        Settings(icd11_mode="mock", icd_client_id="x", icd_client_secret="y").effective_icd11_mode == "mock"
    )


def test_postgres_url_selects_postgres_mode() -> None:
    s = Settings(database_url="postgresql+psycopg://u:p@db:5432/namasetu", redis_url="redis://r:6379/0")
    assert s.db_mode == "postgres"
    assert s.task_mode == "celery-redis"
