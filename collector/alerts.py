"""Persistent alert-rule evaluation and best-effort notification dispatch."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any

from .config import Settings
from .db import Database

logger = logging.getLogger("process_monitor.alerts")


class NotificationDispatcher:
    """Deliver optional notifications without making ingestion brittle.

    Delivery runs off the ingestion request path.  A slow webhook or SMTP
    server must never make an agent retry the same sample indefinitely and
    must never raise an exception out of :meth:`dispatch`.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # Hooks let tests inject fake network layers without monkey-patching
        # the standard library.  The defaults use the real network stack.
        self._send_webhook = self._send_webhook_via_urllib
        self._send_email = self._send_email_via_smtplib

    def dispatch(self, alert: dict[str, Any]) -> None:
        if alert.get("action", "none") == "none":
            return
        thread = threading.Thread(target=self._dispatch_sync, args=(alert,), daemon=True)
        thread.start()

    def _dispatch_sync(self, alert: dict[str, Any]) -> None:
        action = alert.get("action", "none")
        if action == "webhook":
            if not self.settings.webhook_url:
                logger.warning("Webhook action configured but ALERT_WEBHOOK_URL is empty")
                return
            try:
                self._send_webhook(self.settings.webhook_url, alert, self.settings.webhook_timeout_seconds)
                logger.info("Alert webhook delivered", extra={"alert_id": alert.get("id")})
            except Exception:
                logger.exception("Alert webhook delivery failed", extra={"alert_id": alert.get("id")})
        elif action == "email":
            if not (self.settings.smtp_host and self.settings.smtp_to):
                logger.warning("Email action configured but SMTP delivery is not fully configured")
                return
            try:
                self._send_email(alert, self.settings)
                logger.info("Alert email delivered", extra={"alert_id": alert.get("id")})
            except Exception:
                logger.exception("Alert email delivery failed", extra={"alert_id": alert.get("id")})
        else:
            logger.warning("Alert action configured but delivery is not configured", extra={"action": action})

    @staticmethod
    def _send_webhook_via_urllib(url: str, alert: dict[str, Any], timeout: float) -> None:
        body = json.dumps({"event": "alert.triggered", "alert": alert}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured endpoint
            if not 200 <= response.status < 300:
                raise urllib.error.HTTPError(
                    url, response.status, "Webhook returned non-2xx status", {}, None
                )

    @staticmethod
    def _send_email_via_smtplib(alert: dict[str, Any], settings: Settings) -> None:
        message = EmailMessage()
        message["Subject"] = f"Process monitor alert: {alert.get('rule_name', alert.get('rule_id'))}"
        message["From"] = settings.smtp_from
        message["To"] = settings.smtp_to
        # Use ``default=str`` so non-serializable fields (e.g. ``None``) do
        # not break message construction.
        message.set_content(json.dumps(alert, indent=2, default=str))
        if settings.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context
            ) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)


class AlertEngine:
    """Evaluate rules one sample at a time with exact process-instance state."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.dispatcher = NotificationDispatcher(settings)

    def evaluate_sample(self, sample: dict[str, Any]) -> list[dict[str, Any]]:
        """Update rule state for a sample and return newly triggered alerts.

        State keys include host, PID, and create_time. This is the important
        PID-reuse boundary: a new process instance can never inherit the old
        instance's consecutive sample count or active alert.

        Samples whose ``cpu_percent`` is ``None`` (agent has not yet primed a
        CPU baseline for that exact process instance) are skipped for any
        CPU-based rule.  Reporting ``0.0`` for them would let a real
        sustained-CPU process slip past a CPU alert on its first observation.

        All rule evaluation for a sample is delegated to
        :meth:`Database.evaluate_alert_atomic`, which runs the entire
        read-evaluate-write sequence in a single ``BEGIN IMMEDIATE``
        transaction so two simultaneous ingestion requests for the same
        process instance can never create duplicate alerts or corrupt the
        consecutive-sample count.
        """
        triggered: list[dict[str, Any]] = []
        for rule in self.db.list_rules(enabled=True):
            metric = rule["metric"]
            if metric not in sample:
                continue
            raw_value = sample[metric]
            # ``None`` represents "no reading yet" for the metric.  Treat
            # it as unavailable instead of coercing to ``0.0``; that keeps
            # an agent's first sample from accidentally firing (or
            # clearing) an alert based on a phantom value.
            if raw_value is None:
                continue
            try:
                result = self.db.evaluate_alert_atomic(rule=rule, sample=sample)
            except Exception:
                logger.exception(
                    "Atomic alert evaluation failed",
                    extra={"rule_id": rule.get("rule_id"), "host": sample.get("host"), "pid": sample.get("pid")},
                )
                continue
            for alert in result.get("newly_triggered", []):
                triggered.append(alert)
                self.dispatcher.dispatch(alert)
        return triggered
