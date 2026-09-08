"""Persistent alert-rule evaluation and best-effort notification dispatch."""
from __future__ import annotations

import json
import logging
import smtplib
import threading
import urllib.request
from email.message import EmailMessage
from typing import Any

from .config import Settings
from .db import Database

logger = logging.getLogger("process_monitor.alerts")


class NotificationDispatcher:
    """Deliver optional notifications without making ingestion brittle."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def dispatch(self, alert: dict[str, Any]) -> None:
        if alert.get("action", "none") == "none":
            return
        # Delivery runs off the ingestion request path. A slow webhook or SMTP
        # server must never make an agent retry the same sample indefinitely.
        thread = threading.Thread(target=self._dispatch_sync, args=(alert,), daemon=True)
        thread.start()

    def _dispatch_sync(self, alert: dict[str, Any]) -> None:
        action = alert.get("action", "none")
        if action == "webhook" and self.settings.webhook_url:
            try:
                body = json.dumps({"event": "alert.triggered", "alert": alert}).encode("utf-8")
                request = urllib.request.Request(
                    self.settings.webhook_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - configured endpoint
                    logger.info("Alert webhook delivered", extra={"status": response.status, "alert_id": alert.get("id")})
            except Exception:
                logger.exception("Alert webhook delivery failed", extra={"alert_id": alert.get("id")})
        elif action == "email" and self.settings.smtp_host and self.settings.smtp_to:
            try:
                message = EmailMessage()
                message["Subject"] = f"Process monitor alert: {alert.get('rule_name', alert.get('rule_id'))}"
                message["From"] = self.settings.smtp_from
                message["To"] = self.settings.smtp_to
                message.set_content(json.dumps(alert, indent=2, default=str))
                with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=5) as smtp:
                    smtp.starttls()
                    smtp.send_message(message)
                logger.info("Alert email delivered", extra={"alert_id": alert.get("id")})
            except Exception:
                logger.exception("Alert email delivery failed", extra={"alert_id": alert.get("id")})
        elif action != "none":
            logger.warning("Alert action configured but delivery is not configured", extra={"action": action})


class AlertEngine:
    """Evaluate rules one sample at a time with exact process-instance state."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.dispatcher = NotificationDispatcher(settings)

    @staticmethod
    def _matches(value: float, operator: str, threshold: float) -> bool:
        return {
            "gt": value > threshold,
            "gte": value >= threshold,
            "lt": value < threshold,
            "lte": value <= threshold,
            "eq": value == threshold,
        }.get(operator, False)

    def evaluate_sample(self, sample: dict[str, Any]) -> list[dict[str, Any]]:
        """Update rule state for a sample and return newly triggered alerts.

        State keys include host, PID, and create_time. This is the important
        PID-reuse boundary: a new process instance can never inherit the old
        instance's consecutive sample count or active alert.
        """
        triggered: list[dict[str, Any]] = []
        for rule in self.db.list_rules(enabled=True):
            metric = rule["metric"]
            if metric not in sample:
                continue
            value = float(sample[metric])
            timestamp = float(sample["timestamp"])
            state = self.db.get_alert_state(rule["rule_id"], sample["host"], sample["pid"], sample["create_time"])
            if state is None:
                state = {
                    "rule_id": rule["rule_id"],
                    "host": sample["host"],
                    "pid": sample["pid"],
                    "create_time": sample["create_time"],
                    "true_since": None,
                    "consecutive_samples": 0,
                    "last_sample_at": None,
                    "cooldown_until": 0,
                    "active_alert_id": None,
                }

            # Buffered batches can arrive after a newer observation. Keep
            # history, but do not let an older sample rewind a persisted alert
            # streak or resolve an alert based on stale information.
            last_sample_at = state.get("last_sample_at")
            if last_sample_at is not None and timestamp <= float(last_sample_at):
                continue

            condition = self._matches(value, rule["operator"], float(rule["threshold"]))
            if condition:
                if state.get("true_since") is None:
                    state["true_since"] = timestamp
                # A duplicate sample is not evaluated because the database only
                # returns newly inserted rows to the ingestion service. Keeping
                # this calculation simple also makes restarts deterministic.
                state["consecutive_samples"] = int(state.get("consecutive_samples") or 0) + 1
                state["last_sample_at"] = timestamp
                active = self.db.get_active_alert(rule["rule_id"], sample["host"], sample["pid"], sample["create_time"])
                if active:
                    state["active_alert_id"] = active["id"]
                    self.db.update_alert(active["id"], value, timestamp, sample["process_name"], sample.get("username"))
                else:
                    duration_met = timestamp - float(state["true_since"] or timestamp) >= float(rule["duration"])
                    samples_met = int(state["consecutive_samples"]) >= int(rule["consecutive_samples"])
                    cooldown_over = timestamp >= float(state.get("cooldown_until") or 0)
                    if duration_met and samples_met and cooldown_over:
                        alert = self.db.create_alert(
                            {
                                "rule_id": rule["rule_id"],
                                "rule_name": rule["name"],
                                "host": sample["host"],
                                "pid": sample["pid"],
                                "process_name": sample["process_name"],
                                "username": sample.get("username"),
                                "create_time": sample["create_time"],
                                "metric": metric,
                                "operator": rule["operator"],
                                "threshold": rule["threshold"],
                                "current_value": value,
                                "severity": rule["severity"],
                                "triggered_at": timestamp,
                                "last_seen": timestamp,
                                "action": rule["action"],
                            }
                        )
                        state["active_alert_id"] = alert["id"]
                        triggered.append(alert)
                        self.dispatcher.dispatch(alert)
            else:
                active = self.db.get_active_alert(rule["rule_id"], sample["host"], sample["pid"], sample["create_time"])
                if active:
                    self.db.resolve_alert(active["id"], timestamp, value)
                state["true_since"] = None
                state["consecutive_samples"] = 0
                state["last_sample_at"] = timestamp
                state["active_alert_id"] = None
                # Cooldown starts when a triggered condition clears. It is not
                # reset here, so flapping processes cannot spam notifications.
                if active:
                    state["cooldown_until"] = timestamp + float(rule["cooldown"])
            self.db.save_alert_state(state)
        return triggered
