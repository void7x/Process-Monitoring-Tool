from __future__ import annotations

from .conftest import sample


def rule_payload(**overrides):
    payload = {
        "rule_id": "cpu-hot",
        "name": "High CPU",
        "metric": "cpu_percent",
        "operator": "gt",
        "threshold": 80,
        "duration": 0,
        "consecutive_samples": 2,
        "action": "none",
        "cooldown": 60,
        "severity": "critical",
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def ingest(client, payload):
    result = client.post("/ingest", json={"host": "alert-host", "samples": [payload]})
    assert result.status_code == 200, result.text
    return result.json()


def test_consecutive_trigger_and_resolution(client):
    created = client.post("/alerts/rules", json=rule_payload())
    assert created.status_code == 201, created.text
    base = sample(pid=44, create_time=1_800_000_000.0, timestamp=1_800_000_001.0, cpu=90)
    first = ingest(client, base)
    assert first["alerts_triggered"] == 0
    second = ingest(client, {**base, "timestamp": 1_800_000_002.0})
    assert second["alerts_triggered"] == 1
    active = client.get("/alerts", params={"status": "active"}).json()
    assert active["total"] == 1
    assert active["items"][0]["create_time"] == base["create_time"]
    clear = ingest(client, {**base, "timestamp": 1_800_000_003.0, "cpu_percent": 20})
    assert clear["alerts_triggered"] == 0
    resolved = client.get("/alerts", params={"status": "resolved"}).json()
    assert resolved["total"] == 1
    assert resolved["items"][0]["status"] == "resolved"


def test_alert_state_does_not_cross_pid_reuse(client):
    assert client.post("/alerts/rules", json=rule_payload(consecutive_samples=2)).status_code == 201
    old = sample(pid=9, create_time=1_800_000_000.0, timestamp=1_800_000_001.0, cpu=90)
    new = sample(pid=9, create_time=1_800_000_100.0, timestamp=1_800_000_002.0, cpu=90)
    ingest(client, old)
    result = ingest(client, new)
    assert result["alerts_triggered"] == 0
    assert client.get("/alerts", params={"status": "active"}).json()["total"] == 0


def test_rule_crud_and_enabled_flag(client):
    payload = rule_payload(rule_id="memory-watch", name="Memory watch", metric="memory_percent", threshold=50)
    response = client.post("/api/alerts/rules", json=payload)
    assert response.status_code == 201
    rule = client.get("/alerts/rules/memory-watch").json()
    assert rule["enabled"] is True
    update = {**payload, "enabled": False}
    assert client.put("/alerts/rules/memory-watch", json=update).status_code == 200
    assert client.get("/alerts/rules/memory-watch").json()["enabled"] is False
    assert client.delete("/alerts/rules/memory-watch").status_code == 200
    assert client.get("/alerts/rules/memory-watch").status_code == 404
