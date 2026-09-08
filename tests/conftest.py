from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collector.config import Settings
from collector.main import create_app


@pytest.fixture
def app(tmp_path: Path):
    settings = Settings(
        database_path=tmp_path / "monitoring.db",
        stale_after_seconds=30,
        offline_after_seconds=300,
        default_window_seconds=3_600,
        max_ingest_samples=100,
        max_request_bytes=1_000_000,
        retention_days=30,
        cors_origins="*",
    )
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def sample(*, pid=101, create_time=1_700_000_000.0, timestamp=1_700_000_010.0, cpu=12.5, memory=4.0, name="worker.exe"):
    return {
        "pid": pid,
        "process_name": name,
        "username": "operator",
        "create_time": create_time,
        "timestamp": timestamp,
        "cpu_percent": cpu,
        "memory_percent": memory,
        "memory_rss": 50_000_000,
        "read_bytes": 1_000,
        "write_bytes": 2_000,
        "state": "running",
    }
