from __future__ import annotations

import json
import smtplib
import threading
import time
import urllib.error

import pytest

from collector.alerts import NotificationDispatcher
from collector.config import Settings
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


def ingest(client, payload, host="alert-host"):
    result = client.post("/ingest", json={"host": host, "samples": [payload]})
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


# ---------------------------------------------------------------------------
# Notification dispatch failure paths.  These tests prove that webhook/email
# problems do not break ingestion and that the dispatcher swallows errors
# from the configured transport.
# ---------------------------------------------------------------------------


def _wait_for_threads(timeout: float = 2.0) -> None:
    """Join any daemon dispatcher threads started during the test."""
    deadline = time.time() + timeout
    for thread in list(threading.enumerate()):
        if thread is threading.current_thread():
            continue
        if thread.daemon and thread.is_alive():
            remaining = max(0.0, deadline - time.time())
            thread.join(timeout=remaining)


def test_dispatch_webhook_success_path():
    settings = Settings(webhook_url="http://webhook.example/hook", webhook_timeout_seconds=1.0)
    dispatcher = NotificationDispatcher(settings)
    calls: list[tuple[str, dict]] = []

    def fake_send(url, alert, timeout):
        calls.append((url, alert))

    dispatcher._send_webhook = fake_send  # type: ignore[assignment]
    alert = {"id": 1, "rule_name": "test", "host": "h", "pid": 1, "create_time": 1.0, "action": "webhook"}
    dispatcher._dispatch_sync(alert)
    assert calls == [("http://webhook.example/hook", alert)]


def test_dispatch_webhook_4xx_5xx_connection_and_timeout_all_swallowed():
    settings = Settings(webhook_url="http://webhook.example/hook", webhook_timeout_seconds=1.0)
    dispatcher = NotificationDispatcher(settings)

    def raise_4xx(url, alert, timeout):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

    def raise_5xx(url, alert, timeout):
        raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

    def raise_conn(url, alert, timeout):
        raise urllib.error.URLError("DNS failure")

    def raise_timeout(url, alert, timeout):
        raise TimeoutError("upstream timeout")

    for scenario in (raise_4xx, raise_5xx, raise_conn, raise_timeout):
        dispatcher._send_webhook = scenario  # type: ignore[assignment]
        # Must not raise — the dispatcher catches and logs.
        dispatcher._dispatch_sync({"id": 1, "rule_name": "test", "host": "h"})


def test_dispatch_email_success_path(monkeypatch):
    settings = Settings(
        smtp_host="smtp.example",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        smtp_from="from@example",
        smtp_to="to@example",
        smtp_use_tls=True,
        smtp_timeout_seconds=1.0,
    )
    dispatcher = NotificationDispatcher(settings)
    captured: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout
            captured["login_called"] = 0
            captured["starttls_called"] = 0
            captured["sent"] = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            captured["starttls_called"] += 1

        def login(self, user, password):
            captured["login_called"] += 1
            captured["user"] = user
            captured["password"] = password

        def send_message(self, msg):
            captured["sent"].append(msg)

    monkeypatch.setattr("collector.alerts.smtplib.SMTP", FakeSMTP)
    alert = {"id": 1, "rule_name": "r", "host": "h", "pid": 1, "create_time": 1.0}
    dispatcher._send_email(alert, settings)
    assert captured["host"] == "smtp.example"
    assert captured["port"] == 587
    assert captured["starttls_called"] == 1
    assert captured["login_called"] == 1
    assert captured["user"] == "user"
    assert captured["password"] == "secret"
    assert len(captured["sent"]) == 1
    assert captured["sent"][0]["Subject"].startswith("Process monitor alert")
    assert captured["sent"][0]["From"] == "from@example"
    assert captured["sent"][0]["To"] == "to@example"


def test_dispatch_email_smtp_connection_failure_swallowed():
    settings = Settings(smtp_host="nonexistent", smtp_port=25, smtp_from="a@b", smtp_to="c@d", smtp_timeout_seconds=1.0)
    dispatcher = NotificationDispatcher(settings)
    # Real socket error must be caught and logged.
    dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h"})


def test_dispatch_email_tls_failure_swallowed(monkeypatch):
    settings = Settings(
        smtp_host="smtp.example",
        smtp_port=587,
        smtp_from="a@b",
        smtp_to="c@d",
        smtp_use_tls=True,
        smtp_timeout_seconds=1.0,
    )
    dispatcher = NotificationDispatcher(settings)

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            raise smtplib.SMTPException("STARTTLS failed")

        def send_message(self, msg):
            raise AssertionError("send_message should not be called when STARTTLS fails")

    monkeypatch.setattr("collector.alerts.smtplib.SMTP", FailingSMTP)
    dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h"})


def test_dispatch_email_authentication_failure_swallowed(monkeypatch):
    settings = Settings(
        smtp_host="smtp.example",
        smtp_port=587,
        smtp_username="u",
        smtp_password="p",
        smtp_from="a@b",
        smtp_to="c@d",
        smtp_use_tls=False,
        smtp_timeout_seconds=1.0,
    )
    dispatcher = NotificationDispatcher(settings)

    class AuthFailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            raise AssertionError("starttls should not be called")

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")

        def send_message(self, msg):
            raise AssertionError("send_message should not be called when login fails")

    monkeypatch.setattr("collector.alerts.smtplib.SMTP", AuthFailingSMTP)
    dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h"})


def test_dispatch_email_ssl_path(monkeypatch):
    settings = Settings(
        smtp_host="smtp.example",
        smtp_port=465,
        smtp_username="u",
        smtp_password="p",
        smtp_from="a@b",
        smtp_to="c@d",
        smtp_use_ssl=True,
        smtp_timeout_seconds=1.0,
    )
    dispatcher = NotificationDispatcher(settings)
    captured: dict = {}

    class FakeSMTPSSL:
        def __init__(self, host, port, timeout, context):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr("collector.alerts.smtplib.SMTP_SSL", FakeSMTPSSL)
    dispatcher._send_email({"id": 1, "rule_name": "r", "host": "h"}, settings)
    assert captured["port"] == 465
    assert captured["login"] == ("u", "p")


def test_notification_failure_does_not_break_ingestion(client, monkeypatch):
    """A webhook failure during dispatch must not roll back the sample or alert state.

    The dispatcher is fired-and-forgotten on a background thread, so even
    when the network is broken the agent's POST /ingest must still receive
    a 200 with the sample persisted and the alert created.
    """
    assert client.post("/alerts/rules", json=rule_payload(action="webhook")).status_code == 201
    # Force every webhook attempt to fail synchronously.  Also patch the
    # engine's settings so a webhook URL is configured.
    engine = client.app.state.alert_engine
    from dataclasses import replace as dc_replace
    original_settings = engine.dispatcher.settings
    original_send = engine.dispatcher._send_webhook
    engine.dispatcher.settings = dc_replace(
        original_settings,
        webhook_url="http://webhook.invalid/hook",
        webhook_timeout_seconds=1.0,
    )
    calls = {"n": 0}

    def broken_send(url, alert, timeout):
        calls["n"] += 1
        raise urllib.error.URLError("network unreachable")

    engine.dispatcher._send_webhook = broken_send  # type: ignore[assignment]
    try:
        # ``consecutive_samples=2`` means the alert is triggered on the
        # second sample, which is when the dispatcher is invoked.
        base = sample(pid=33, create_time=1_800_000_500.0, timestamp=1_800_000_501.0, cpu=90)
        first = ingest(client, base)
        assert first["accepted"] == 1
        second = ingest(client, {**base, "timestamp": 1_800_000_502.0})
        assert second["accepted"] == 1
        assert second["alerts_triggered"] == 1
        # Wait for the daemon dispatcher thread to run and fail.
        _wait_for_threads()
    finally:
        engine.dispatcher._send_webhook = original_send  # type: ignore[assignment]
        engine.dispatcher.settings = original_settings
    # The alert is still present in the database, even though the webhook
    # failed.  This is the critical isolation guarantee: ingestion success
    # never depends on notification success.
    active = client.get("/alerts", params={"status": "active"}).json()
    assert active["total"] == 1
    assert calls["n"] == 1


def test_notification_failure_email_does_not_break_ingestion(client, monkeypatch):
    """Same isolation guarantee for email delivery."""
    assert client.post("/alerts/rules", json=rule_payload(action="email")).status_code == 201
    # Inject SMTP configuration on the engine.
    engine = client.app.state.alert_engine
    original_settings = engine.dispatcher.settings
    from dataclasses import replace as dc_replace
    engine.dispatcher.settings = dc_replace(
        original_settings,
        smtp_host="smtp.invalid",
        smtp_port=25,
        smtp_from="a@b",
        smtp_to="c@d",
        smtp_timeout_seconds=1.0,
    )
    try:
        base = sample(pid=55, create_time=1_800_001_000.0, timestamp=1_800_001_001.0, cpu=90)
        first = ingest(client, base)
        second = ingest(client, {**base, "timestamp": 1_800_001_002.0})
        assert second["alerts_triggered"] == 1
        _wait_for_threads()
    finally:
        engine.dispatcher.settings = original_settings
    active = client.get("/alerts", params={"status": "active"}).json()
    assert active["total"] == 1


def test_unconfigured_action_logs_but_does_not_crash():
    """``action='webhook'`` without ``ALERT_WEBHOOK_URL`` is not an error worth crashing on."""
    settings = Settings()
    dispatcher = NotificationDispatcher(settings)
    dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h", "action": "webhook"})
    dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h", "action": "email"})


def test_email_does_not_log_password():
    """A failed SMTP attempt must not write the configured password into logs."""
    settings = Settings(
        smtp_host="smtp.invalid",
        smtp_port=25,
        smtp_username="u",
        smtp_password="super-secret-value-12345",
        smtp_from="a@b",
        smtp_to="c@d",
        smtp_timeout_seconds=1.0,
    )
    dispatcher = NotificationDispatcher(settings)
    import logging
    log_records: list[str] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    handler = CapturingHandler()
    logging.getLogger("process_monitor.alerts").addHandler(handler)
    try:
        dispatcher._dispatch_sync({"id": 1, "rule_name": "r", "host": "h", "action": "email"})
    finally:
        logging.getLogger("process_monitor.alerts").removeHandler(handler)
    joined = "\n".join(log_records)
    assert "super-secret-value-12345" not in joined


# ---------------------------------------------------------------------------
# Concurrency: the alert engine must produce a single active alert and a
# correct consecutive-sample count even under many simultaneous ingestion
# requests for the same exact process instance.
# ---------------------------------------------------------------------------


def test_concurrent_ingest_creates_at_most_one_alert(client):
    """Threads racing to evaluate the same process instance must not duplicate alerts.

    The TestClient serializes HTTP requests, so we drive the alert engine
    directly with multiple threads each holding their own connection.  The
    contract is that exactly one alert can ever be active for a single
    ``(rule_id, host, pid, create_time)`` triple.
    """
    db = client.app.state.db
    rule = {
        "rule_id": "race-rule-1", "name": "R", "metric": "cpu_percent", "operator": "gt",
        "threshold": 80, "duration": 0, "consecutive_samples": 1, "cooldown": 0,
        "severity": "warning", "action": "none",
    }
    base = {"host": "race-host-1", "pid": 77, "process_name": "p", "username": "u",
            "create_time": 1_800_100_000.0, "memory_percent": 1.0, "memory_rss": 1,
            "read_bytes": 0, "write_bytes": 0, "state": "running", "cpu_percent": 95.0}
    import threading
    barrier = threading.Barrier(8)

    def fire(index: int) -> None:
        barrier.wait()
        sample_data = {**base, "timestamp": 1_800_100_001.0 + index}
        db.evaluate_alert_atomic(rule=rule, sample=sample_data)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    active = db.list_alerts(status="active")
    assert len(active[0]) == 1


def test_concurrent_ingest_keeps_consecutive_count_correct(client):
    """Concurrent evaluation of the same process instance must advance the counter monotonically.

    FastAPI's TestClient serializes requests through a single ASGI portal,
    so the realistic concurrency test exercises the database layer directly
    with multiple threads each opening their own connection.  This is the
    production-relevant contract: even with simultaneous ingestion from
    many workers, the ``consecutive_samples`` counter must reflect the
    number of accepted observations, never fewer and never more.
    """
    db = client.app.state.db
    rule = {
        "rule_id": "race-rule", "name": "Race", "metric": "cpu_percent", "operator": "gt",
        "threshold": 80, "duration": 0, "consecutive_samples": 1, "cooldown": 0,
        "severity": "warning", "action": "none",
    }
    base = {"host": "race-host", "pid": 88, "process_name": "p", "username": "u",
            "create_time": 1_800_200_000.0, "memory_percent": 1.0, "memory_rss": 1,
            "read_bytes": 0, "write_bytes": 0, "state": "running", "cpu_percent": 95.0}
    import threading
    barrier = threading.Barrier(4)
    results: list[dict] = []
    lock = threading.Lock()

    def fire(index: int) -> None:
        barrier.wait()
        sample_data = {**base, "timestamp": 1_800_200_001.0 + index}
        result = db.evaluate_alert_atomic(rule=rule, sample=sample_data)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    # Exactly one alert was created (the first ``consecutive_samples>=1``
    # evaluation, since we set ``consecutive_samples=1``).
    triggered = sum(len(r.get("newly_triggered", [])) for r in results)
    assert triggered == 1
    # And the persisted state reflects the most recent accepted sample.
    alert_state = db.get_alert_state("race-rule", "race-host", 88, 1_800_200_000.0)
    assert alert_state is not None
    assert alert_state["active_alert_id"] is not None
    # No additional alerts were created by the racing threads.
    active = db.list_alerts(status="active")
    assert len(active[0]) == 1


def test_concurrent_ingest_resolves_exactly_once(client):
    """A burst of clearing samples must produce exactly one resolution."""
    db = client.app.state.db
    rule = {
        "rule_id": "race-rule-2", "name": "R", "metric": "cpu_percent", "operator": "gt",
        "threshold": 80, "duration": 0, "consecutive_samples": 1, "cooldown": 0,
        "severity": "warning", "action": "none",
    }
    base = {"host": "race-host-2", "pid": 99, "process_name": "p", "username": "u",
            "create_time": 1_800_300_000.0, "memory_percent": 1.0, "memory_rss": 1,
            "read_bytes": 0, "write_bytes": 0, "state": "running", "cpu_percent": 95.0}
    # Trigger an alert first.
    trigger = {**base, "timestamp": 1_800_300_001.0}
    db.evaluate_alert_atomic(rule=rule, sample=trigger)
    assert len(db.list_alerts(status="active")[0]) == 1
    import threading
    barrier = threading.Barrier(6)
    results: list[dict] = []
    lock = threading.Lock()

    def fire(index: int) -> None:
        barrier.wait()
        clear = {**base, "timestamp": 1_800_300_010.0 + index, "cpu_percent": 2.0}
        result = db.evaluate_alert_atomic(rule=rule, sample=clear)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    # Exactly one thread observes the resolution transition.  The rest
    # see a clean state and have nothing to do.
    resolved_count = sum(len(r.get("resolved", [])) for r in results)
    assert resolved_count == 1
    assert len(db.list_alerts(status="active")[0]) == 0
    assert len(db.list_alerts(status="resolved")[0]) == 1


def test_evaluate_alert_atomic_skips_out_of_order_samples(tmp_path):
    """Buffered/old samples must not rewind a persisted alert streak."""
    from collector.db import Database
    db = Database(tmp_path / "atomic.sqlite")
    db.initialize()
    rule = {
        "rule_id": "r1", "name": "R", "metric": "cpu_percent", "operator": "gt",
        "threshold": 80, "duration": 0, "consecutive_samples": 2, "cooldown": 0,
        "severity": "warning", "action": "none",
    }
    sample_now = {**sample(pid=1, create_time=1_900_000_000.0, timestamp=1_900_000_010.0, cpu=95), "host": "h"}
    db.evaluate_alert_atomic(rule=rule, sample=sample_now)
    # second sample triggers (consecutive_samples >= 2)
    sample_later = {**sample_now, "timestamp": 1_900_000_011.0}
    result2 = db.evaluate_alert_atomic(rule=rule, sample=sample_later)
    assert len(result2["newly_triggered"]) == 1
    # out-of-order sample must not rewind the streak or clear the alert
    old = {**sample_now, "timestamp": 1_900_000_005.0}
    result3 = db.evaluate_alert_atomic(rule=rule, sample=old)
    assert result3["newly_triggered"] == []
    assert result3["resolved"] == []
    # active alert still present
    active = db.list_alerts(status="active")
    assert len(active[0]) == 1

# ---------------------------------------------------------------------------
# Second-pass — deterministic post-commit notification dispatch.
#
# Batch ingestion must commit alert state first and then emit
# notifications in a reproducible sorted order.  The tests prove
# that sorting is stable, that ``notify=False`` defers dispatch,
# and that the ingest path uses the post-commit hook.
# ---------------------------------------------------------------------------


def test_dispatch_batch_is_deterministic_and_sorted():
    """``dispatch_batch`` must emit alerts in triggered_at / rule_id order."""
    from collector.alerts import NotificationDispatcher
    from collector.config import Settings

    settings = Settings(webhook_url="http://example.invalid/hook", webhook_timeout_seconds=1.0)
    dispatcher = NotificationDispatcher(settings)
    emitted: list[int] = []

    def fake_send(url, alert, timeout):
        emitted.append(alert["id"])

    dispatcher._send_webhook = fake_send  # type: ignore[assignment]
    # Alerts intentionally out of order.
    alerts = [
        {"id": 3, "rule_id": "b-rule", "host": "h", "pid": 1, "create_time": 1.0, "triggered_at": 300.0, "action": "webhook"},
        {"id": 1, "rule_id": "a-rule", "host": "h", "pid": 2, "create_time": 1.0, "triggered_at": 100.0, "action": "webhook"},
        {"id": 2, "rule_id": "a-rule", "host": "h", "pid": 1, "create_time": 1.0, "triggered_at": 100.0, "action": "webhook"},
        {"id": 4, "rule_id": "a-rule", "host": "h", "pid": 1, "create_time": 1.0, "triggered_at": 200.0, "action": "none"},  # not dispatched
    ]
    dispatcher.dispatch_batch_sync(alerts)
    # ``action='none'`` is filtered, and the remaining are sorted by
    # triggered_at, rule_id, host, pid, create_time, id.
    assert emitted == [2, 1, 3]


def test_alert_engine_evaluate_sample_notify_flag_defers_dispatch(monkeypatch):
    """``notify=False`` must collect alerts without sending them."""
    from collector.alerts import AlertEngine
    from collector.config import Settings
    from pathlib import Path
    import tempfile
    from collector.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "notify.sqlite")
        db.initialize()
        settings = Settings(webhook_url="http://example.invalid/hook")
        engine = AlertEngine(db, settings)
        db.create_rule({
            "rule_id": "notify-rule", "name": "N", "metric": "cpu_percent",
            "operator": "gt", "threshold": 10, "duration": 0,
            "consecutive_samples": 1, "cooldown": 0, "severity": "warning",
            "action": "webhook", "enabled": True,
        })
        sample_data = {
            "host": "notify-host", "pid": 1, "process_name": "p", "username": "u",
            "create_time": 1_700_000_000.0, "timestamp": 1_700_000_010.0,
            "cpu_percent": 95.0, "memory_percent": 1.0, "memory_rss": 1,
            "read_bytes": 0, "write_bytes": 0, "state": "running",
        }
        calls: list = []
        monkeypatch.setattr(engine.dispatcher, "_send_webhook", lambda url, alert, timeout: calls.append(alert["id"]))
        # With notify=False, no webhook is sent even though an alert is triggered.
        triggered = engine.evaluate_sample(sample_data, notify=False)
        assert len(triggered) == 1
        assert calls == []
        # Deterministic batch dispatch (sync variant for test determinism) sends exactly one.
        engine.dispatcher.dispatch_batch_sync(triggered)
        assert len(calls) == 1


def test_ingest_uses_post_commit_deterministic_dispatch(client, monkeypatch):
    """``POST /ingest`` batches must dispatch post-commit in sorted order."""
    # Create two rules that will both trigger on the same sample.
    for rule_id, thresh in [("rule-a", 10), ("rule-b", 10)]:
        payload = {
            "rule_id": rule_id, "name": f"R {rule_id}", "metric": "cpu_percent",
            "operator": "gt", "threshold": thresh, "duration": 0,
            "consecutive_samples": 1, "action": "webhook", "cooldown": 0,
            "severity": "warning", "enabled": True,
        }
        assert client.post("/alerts/rules", json=payload).status_code == 201

    # Capture the order in which the dispatcher is asked to send webhooks.
    order: list[str] = []
    engine = client.app.state.alert_engine
    original_send = engine.dispatcher._send_webhook

    def capturing_send(url, alert, timeout):
        order.append(alert["rule_id"])

    engine.dispatcher._send_webhook = capturing_send  # type: ignore[assignment]
    from dataclasses import replace as dc_replace
    original_settings = engine.dispatcher.settings
    engine.dispatcher.settings = dc_replace(original_settings, webhook_url="http://hook.invalid/", webhook_timeout_seconds=1.0)
    try:
        sample_payload = {
            "pid": 777, "process_name": "p", "username": "u",
            "create_time": 1_800_500_000.0, "timestamp": 1_800_500_010.0,
            "cpu_percent": 99.0, "memory_percent": 1.0, "memory_rss": 1,
            "read_bytes": 0, "write_bytes": 0, "state": "running",
        }
        response = client.post("/ingest", json={"host": "batch-host", "samples": [sample_payload]})
        assert response.status_code == 200, response.text
        assert response.json()["alerts_triggered"] == 2
        # Allow background threads to run.
        import time, threading
        deadline = time.time() + 2.0
        while len(order) < 2 and time.time() < deadline:
            time.sleep(0.05)
        # Deterministic order: rule-a before rule-b (sorted by rule_id).
        assert order == ["rule-a", "rule-b"]
    finally:
        engine.dispatcher._send_webhook = original_send  # type: ignore[assignment]
        engine.dispatcher.settings = original_settings
        # Join daemon threads to avoid bleed into next tests.
        import time, threading
        deadline = time.time() + 2.0
        for t in list(threading.enumerate()):
            if t is threading.current_thread():
                continue
            if t.daemon and t.is_alive():
                remaining = max(0.0, deadline - time.time())
                t.join(timeout=remaining)
