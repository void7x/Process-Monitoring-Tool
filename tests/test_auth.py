from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from collector.config import Settings
from collector.main import create_app
from .conftest import sample


@pytest.fixture
def auth_app(tmp_path):
    settings = Settings(
        database_path=tmp_path / "auth.sqlite",
        stale_after_seconds=30,
        offline_after_seconds=300,
        default_window_seconds=3_600,
        max_ingest_samples=100,
        max_request_bytes=1_000_000,
        retention_days=30,
        cors_origins="*",
        auth_token="test-secret-token-12345",
    )
    return create_app(settings)


@pytest.fixture
def auth_client(auth_app):
    with TestClient(auth_app) as client:
        yield client


def test_health_and_ready_are_public_when_auth_enabled(auth_client):
    """``/health`` and ``/ready`` are always reachable for orchestrators."""
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/ready").status_code == 200


def test_summary_without_token_is_rejected(auth_client):
    response = auth_client.get("/summary")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "Bearer" in response.headers.get("www-authenticate", "")


def test_summary_with_invalid_token_is_rejected(auth_client):
    response = auth_client.get("/summary", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_summary_with_valid_bearer_token_is_accepted(auth_client):
    response = auth_client.get("/summary", headers={"Authorization": "Bearer test-secret-token-12345"})
    assert response.status_code == 200


def test_summary_with_x_auth_token_header_is_accepted(auth_client):
    response = auth_client.get("/summary", headers={"X-Auth-Token": "test-secret-token-12345"})
    assert response.status_code == 200


def test_ingest_requires_token(auth_client):
    response = auth_client.post("/ingest", json={"host": "h", "samples": [sample()]})
    assert response.status_code == 401


def test_ingest_with_valid_token_works(auth_client):
    response = auth_client.post(
        "/ingest",
        json={"host": "h", "samples": [sample()]},
        headers={"Authorization": "Bearer test-secret-token-12345"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1


def test_auth_disabled_by_default_does_not_block_requests(app, client):
    """When no ``AUTH_TOKEN`` is configured, every endpoint is public."""
    # ``app`` / ``client`` use the empty default settings from conftest.
    assert client.get("/summary").status_code == 200
    assert client.post("/ingest", json={"host": "h", "samples": [sample()]}).status_code == 200


def test_auth_disabled_with_only_whitespace_token(tmp_path):
    settings = Settings(
        database_path=tmp_path / "ws.sqlite",
        auth_token="   ",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # Whitespace-only tokens must be treated as "no token configured" so
        # an operator that forgets to set the env var cannot accidentally
        # lock themselves out.
        assert client.get("/summary").status_code == 200


def test_auth_protect_docs_blocks_openapi_when_enabled(tmp_path):
    settings = Settings(
        database_path=tmp_path / "docs.sqlite",
        auth_token="docs-token",
        auth_protect_docs=True,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 401
        # With the token, OpenAPI is reachable.
        assert client.get("/openapi.json", headers={"Authorization": "Bearer docs-token"}).status_code == 200


def test_auth_does_not_log_token_value(tmp_path, caplog):
    settings = Settings(
        database_path=tmp_path / "log.sqlite",
        auth_token="must-never-be-logged-abc",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with caplog.at_level("INFO", logger="process_monitor.auth"):
            client.get("/summary", headers={"Authorization": "Bearer not-the-token"})
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "must-never-be-logged-abc" not in joined
    assert "not-the-token" not in joined
