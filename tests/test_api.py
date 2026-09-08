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


# ---------------------------------------------------------------------------
# Fix 2 — the "Running Processes" KPI must reflect processes that are
# actually in the ``running`` state, not every latest process record.
# ---------------------------------------------------------------------------


def _ingest_with_state(client, host, pid, create_time, timestamp, state):
    payload = sample(pid=pid, create_time=create_time, timestamp=timestamp)
    payload["state"] = state
    response = client.post("/ingest", json={"host": host, "samples": [payload]})
    assert response.status_code == 200


def test_running_processes_kpi_counts_only_running_state(client):
    """``total_running_processes`` is a count of *running* process instances only."""
    host = "kpi-host"
    # 2 running + 1 sleeping + 1 stopped.
    _ingest_with_state(client, host, 1, 1_700_000_000.0, 1_700_000_010.0, "running")
    _ingest_with_state(client, host, 2, 1_700_000_001.0, 1_700_000_011.0, "running")
    _ingest_with_state(client, host, 3, 1_700_000_002.0, 1_700_000_012.0, "sleeping")
    _ingest_with_state(client, host, 4, 1_700_000_003.0, 1_700_000_013.0, "stopped")
    kpis = client.get("/summary").json()["kpis"]
    assert kpis["total_running_processes"] == 2


def test_running_processes_kpi_includes_only_latest_per_instance(client):
    """An older sample for the same process instance must not be double-counted."""
    host = "kpi-host-2"
    _ingest_with_state(client, host, 1, 1_700_001_000.0, 1_700_001_010.0, "sleeping")
    _ingest_with_state(client, host, 1, 1_700_001_000.0, 1_700_001_020.0, "running")
    kpis = client.get("/summary").json()["kpis"]
    # Only one process instance exists, and its latest sample says
    # running — so the count is 1, not 2.
    assert kpis["total_running_processes"] == 1


def test_running_processes_kpi_empty_host(client):
    """An empty host contributes zero to the running-processes KPI."""
    # No data ingested at all.
    kpis = client.get("/summary").json()["kpis"]
    assert kpis["total_running_processes"] == 0


def test_running_processes_kpi_excludes_unknown_state(client):
    """``state='unknown'`` and blank states are not counted as running."""
    host = "kpi-host-3"
    _ingest_with_state(client, host, 1, 1_700_002_000.0, 1_700_002_010.0, "unknown")
    _ingest_with_state(client, host, 2, 1_700_002_001.0, 1_700_002_011.0, "idle")
    kpis = client.get("/summary").json()["kpis"]
    assert kpis["total_running_processes"] == 0


def test_running_processes_kpi_does_not_count_historical_samples(client):
    """The KPI is computed from ``latest`` per instance, not from history."""
    host = "kpi-host-4"
    # Three running samples, all the same instance.
    for index in range(3):
        _ingest_with_state(client, host, 1, 1_700_003_000.0, 1_700_003_010.0 + index, "running")
    kpis = client.get("/summary").json()["kpis"]
    assert kpis["total_running_processes"] == 1
