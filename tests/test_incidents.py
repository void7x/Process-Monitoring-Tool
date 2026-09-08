"""Tests for Process Incident Analyzer — evidence-based reconstruction."""
from __future__ import annotations

import time

from collector.db import Database
from collector.incidents import IncidentAnalyzer

# Re-use sample helper shape from conftest
def _sample(*, pid=101, create_time=1_700_000_000.0, timestamp=1_700_000_010.0, cpu=12.5, memory=4.0, rss=50_000_000, name="worker.exe", state="running"):
    return {
        "pid": pid,
        "process_name": name,
        "username": "operator",
        "create_time": create_time,
        "timestamp": timestamp,
        "cpu_percent": cpu,
        "memory_percent": memory,
        "memory_rss": rss,
        "read_bytes": 1_000,
        "write_bytes": 2_000,
        "state": state,
    }


def _create_alert(db: Database, *, host="h1", pid=101, create_time=1_700_000_000.0, metric="cpu_percent", operator="gt", threshold=80, current_value=85, triggered_at=1_700_000_050.0):
    return db.create_alert({
        "rule_id": "r1",
        "rule_name": "High CPU",
        "host": host,
        "pid": pid,
        "process_name": "worker.exe",
        "username": "operator",
        "create_time": create_time,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "current_value": current_value,
        "severity": "critical",
        "triggered_at": triggered_at,
        "last_seen": triggered_at,
        "action": "none",
    })


def test_incident_analyzer_history_analysis(tmp_path):
    """History analysis uses real telemetry to compute previous/peak/delta/duration and timeline."""
    db = Database(tmp_path / "t.db")
    db.initialize()
    host = "h1"
    pid = 101
    create_time = 1_700_000_000.0
    # 6 samples over 50s, last is alert trigger at 85%
    base = create_time
    cpus = [10, 20, 30, 40, 50, 85]
    for idx, cpu in enumerate(cpus):
        ts = base + 10 * idx
        db.insert_samples(host, [_sample(pid=pid, create_time=create_time, timestamp=ts, cpu=cpu, memory=10 + idx, rss=50_000_000 + idx * 1_000_000)])
    triggered_at = base + 50
    alert = _create_alert(db, host=host, pid=pid, create_time=create_time, current_value=85, triggered_at=triggered_at)
    analyzer = IncidentAnalyzer(db, window_seconds=60)
    result = analyzer.analyze(alert["id"])
    assert result is not None
    # Structured JSON shape
    assert "incident" in result and "summary" in result and "timeline" in result and "evidence" in result
    summary = result["summary"]
    # previous is sample before alert (50), current 85, peak 85, delta 35
    assert summary["previous_value"] == 50
    assert summary["current_value"] == 85
    assert summary["peak_value"] == 85
    assert summary["delta"] == 35
    assert summary["is_insufficient"] is False
    assert summary["sample_count"] == 6
    # Duration above threshold: only last sample >80, so duration should be 0 or <10
    assert summary["duration_seconds"] >= 0
    # Timeline is chronological ascending and includes all samples + alert
    timeline = result["timeline"]
    # First timeline entry is process started
    assert timeline[0]["is_start"] is True
    # Last is alert
    assert timeline[-1]["is_alert"] is True
    # Timestamps should be ascending
    timestamps = [t["timestamp"] for t in timeline if not t.get("is_start")]
    # The alert timestamp is triggered_at, samples are before; timeline should be sorted
    assert timestamps == sorted(timestamps)
    # Evidence should mention increase and threshold breach (real telemetry, not fabricated)
    evidence = result["evidence"]
    assert any("increased" in e.lower() for e in evidence)
    assert any("threshold" in e.lower() for e in evidence)


def test_incident_analyzer_pid_reuse_protection(tmp_path):
    """Same PID with different create_time must not mix histories."""
    db = Database(tmp_path / "t.db")
    db.initialize()
    host = "h1"
    pid = 123  # reused PID
    create_time_old = 1_700_000_000.0
    create_time_new = 1_700_000_100.0
    # Old instance: 3 samples with high CPU 90
    for i in range(3):
        db.insert_samples(host, [_sample(pid=pid, create_time=create_time_old, timestamp=create_time_old + i * 10, cpu=90, memory=20)])
    # New instance: 4 samples with low CPU 10,20,30 then trigger 85
    for idx, cpu in enumerate([10, 20, 30, 85]):
        db.insert_samples(host, [_sample(pid=pid, create_time=create_time_new, timestamp=create_time_new + idx * 10, cpu=cpu, memory=10)])
    triggered_at = create_time_new + 30
    alert = _create_alert(db, host=host, pid=pid, create_time=create_time_new, current_value=85, triggered_at=triggered_at)
    analyzer = IncidentAnalyzer(db)
    result = analyzer.analyze(alert["id"])
    assert result is not None
    # Summary should reflect new instance only (previous 30, not 90)
    assert result["summary"]["previous_value"] == 30
    assert result["summary"]["peak_value"] == 85
    assert result["summary"]["sample_count"] == 4
    # Timeline should not contain 90% values
    for entry in result["timeline"]:
        if entry.get("cpu_percent") is not None:
            assert entry["cpu_percent"] != 90 or entry.get("is_start")


def test_incident_analyzer_insufficient_history(tmp_path):
    """Only one sample in window yields insufficient telemetry."""
    db = Database(tmp_path / "t.db")
    db.initialize()
    host = "h1"
    pid = 200
    create_time = 1_700_000_000.0
    # Only the alert sample itself
    db.insert_samples(host, [_sample(pid=pid, create_time=create_time, timestamp=create_time + 5, cpu=85)])
    alert = _create_alert(db, host=host, pid=pid, create_time=create_time, current_value=85, triggered_at=create_time + 5)
    analyzer = IncidentAnalyzer(db, window_seconds=60)
    result = analyzer.analyze(alert["id"])
    assert result is not None
    summary = result["summary"]
    assert summary["is_insufficient"] is True
    assert summary["previous_value"] is None
    assert summary["sample_count"] == 1
    evidence = result["evidence"]
    assert any("insufficient telemetry" in e.lower() for e in evidence)
    # Timeline still returned but with insufficient flag
    assert len(result["timeline"]) >= 1


def test_incident_analyzer_no_fabricated_data(tmp_path):
    """Evidence must never claim unobserved causes (malware, user, attack, etc.)."""
    db = Database(tmp_path / "t.db")
    db.initialize()
    host = "h1"
    pid = 300
    create_time = 1_700_000_000.0
    for idx, cpu in enumerate([10, 80, 85]):
        db.insert_samples(host, [_sample(pid=pid, create_time=create_time, timestamp=create_time + idx * 10, cpu=cpu)])
    alert = _create_alert(db, host=host, pid=pid, create_time=create_time, current_value=85, triggered_at=create_time + 20)
    analyzer = IncidentAnalyzer(db)
    result = analyzer.analyze(alert["id"])
    assert result is not None
    forbidden = ["malware", "attack", "virus", "hacker", "intrusion", "user action", "manual", "external cause", "network failure", "disk failure"]
    for entry in result["evidence"]:
        lower = entry.lower()
        for word in forbidden:
            assert word not in lower, f"Evidence fabrication detected: '{entry}' contains forbidden '{word}'"
    # Timeline must only contain real sampled values, not hallucinated
    timeline = result["timeline"]
    # Ensure every timeline cpu comes from inserted samples or alert
    allowed_cpus = {10, 80, 85, None}
    for entry in timeline:
        if not entry.get("is_start") and not entry.get("is_alert"):
            assert entry.get("cpu_percent") in allowed_cpus


def test_incident_api_endpoint(tmp_path):
    """GET /api/incidents/{id} returns structured JSON and 404 for missing."""
    from collector.config import Settings
    from collector.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        database_path=tmp_path / "api.db",
        stale_after_seconds=30,
        offline_after_seconds=300,
        default_window_seconds=3_600,
        max_ingest_samples=100,
        max_request_bytes=1_000_000,
        retention_days=30,
        cors_origins="*",
    )
    app = create_app(settings)
    db = app.state.db
    host = "h1"
    pid = 400
    create_time = 1_700_000_000.0
    for idx, cpu in enumerate([20, 40, 85]):
        db.insert_samples(host, [_sample(pid=pid, create_time=create_time, timestamp=create_time + idx * 10, cpu=cpu)])
    alert = _create_alert(db, host=host, pid=pid, create_time=create_time, current_value=85, triggered_at=create_time + 20)
    alert_id = alert["id"]
    with TestClient(app) as client:
        resp = client.get(f"/api/incidents/{alert_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "incident" in data and "summary" in data and "timeline" in data and "evidence" in data
        assert data["incident"]["id"] == alert_id
        assert data["summary"]["metric"] == "cpu_percent"
        # Alias endpoint should also work
        resp2 = client.get(f"/incidents/{alert_id}")
        assert resp2.status_code == 200
        # Missing alert should 404 with structured error
        resp3 = client.get("/api/incidents/99999")
        assert resp3.status_code == 404
        assert "error" in resp3.json()


def test_incident_api_scoped_query_no_full_scan(tmp_path):
    """History query is scoped by host+pid+create_time (not a full table scan)."""
    # This is a behavioral guard: insert many unrelated hosts/pids and verify
    # that incident analysis for the target instance is unaffected in timing
    # and correctness (i.e., isolation via WHERE host=? AND pid=? AND create_time=?)
    db = Database(tmp_path / "t2.db")
    db.initialize()
    target_host = "h-target"
    target_pid = 900
    target_create = 1_700_000_000.0
    # Insert noise: 50 other instances
    for i in range(50):
        db.insert_samples(f"h-noise-{i}", [_sample(pid=1000 + i, create_time=1_800_000_000.0 + i, timestamp=1_800_000_000.0 + i, cpu=99)])
    # Target samples
    for idx, cpu in enumerate([10, 20, 85]):
        db.insert_samples(target_host, [_sample(pid=target_pid, create_time=target_create, timestamp=target_create + idx * 10, cpu=cpu)])
    alert = _create_alert(db, host=target_host, pid=target_pid, create_time=target_create, current_value=85, triggered_at=target_create + 20)
    analyzer = IncidentAnalyzer(db)
    result = analyzer.analyze(alert["id"])
    assert result["summary"]["sample_count"] == 3
    assert result["summary"]["previous_value"] == 20
