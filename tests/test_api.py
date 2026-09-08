from __future__ import annotations

import time

from .conftest import sample


def ingest(client, host="dev-box", samples=None):
    response = client.post("/ingest", json={"host": host, "samples": samples or [sample()]})
    assert response.status_code == 200, response.text
    return response.json()


def test_health_and_validation(client):
    assert client.get("/health").json()["status"] == "ok"
    invalid = client.post("/ingest", json={"host": "", "samples": []})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


def test_duplicate_ingest_is_idempotent_and_history_is_available(client):
    payload = sample(timestamp=1_700_000_010.0)
    first = ingest(client, samples=[payload])
    second = ingest(client, samples=[payload])
    assert first["accepted"] == 1
    assert second["accepted"] == 0
    assert second["duplicates"] == 1
    history = client.get("/history", params={"host": "dev-box"}).json()
    assert history["total"] == 1


def test_pid_reuse_keeps_process_instance_history_isolated(client):
    now = time.time()
    old = sample(timestamp=now - 10.0, create_time=now - 100.0, name="old-worker")
    new = sample(timestamp=now - 1.0, create_time=now - 50.0, name="new-worker")
    ingest(client, samples=[old, new])
    old_detail = client.get("/processes/dev-box/101", params={"create_time": old["create_time"]})
    new_detail = client.get("/processes/dev-box/101", params={"create_time": new["create_time"]})
    assert old_detail.status_code == 200
    assert new_detail.status_code == 200
    assert old_detail.json()["identity"]["process_name"] == "old-worker"
    assert new_detail.json()["identity"]["process_name"] == "new-worker"
    assert len(old_detail.json()["history"]) == 1
    assert len(new_detail.json()["history"]) == 1


def test_summary_processes_and_compatibility_metrics(client):
    ingest(client, host="app-01", samples=[sample(pid=1, timestamp=1_700_000_010.0), sample(pid=2, timestamp=1_700_000_011.0)])
    assert client.get("/api/summary").status_code == 200
    processes = client.get("/processes", params={"host": "app-01"}).json()
    assert processes["total"] == 2
    metrics = client.get("/metrics", params={"host": "app-01"}).json()
    assert metrics["total"] == 2
    assert client.get("/api/hosts/app-01").status_code == 200
