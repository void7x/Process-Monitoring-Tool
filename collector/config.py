"""Configuration for the process monitoring collector.

The collector intentionally uses environment variables rather than hard-coded
operational settings so the same application can run locally, in Docker, or as
an installed service.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    """Runtime settings with conservative, safe defaults."""

    database_path: Path = Path(os.getenv("DB_PATH", "data/monitoring.db"))
    stale_after_seconds: float = _float_env("STALE_AFTER_SECONDS", 90, 15, 86_400)
    offline_after_seconds: float = _float_env("OFFLINE_AFTER_SECONDS", 900, 60, 604_800)
    default_window_seconds: int = _int_env("DEFAULT_WINDOW_SECONDS", 3_600, 60, 604_800)
    max_ingest_samples: int = _int_env("MAX_INGEST_SAMPLES", 500, 1, 10_000)
    max_request_bytes: int = _int_env("MAX_REQUEST_BYTES", 5_000_000, 100_000, 50_000_000)
    retention_days: int = _int_env("RETENTION_DAYS", 30, 1, 3_650)
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    auth_token: str = os.getenv("AUTH_TOKEN", "")
    auth_protect_docs: bool = os.getenv("AUTH_PROTECT_DOCS", "false").lower() in {"1", "true", "yes", "on"}
    webhook_url: str = os.getenv("ALERT_WEBHOOK_URL", "")
    webhook_timeout_seconds: float = _float_env("ALERT_WEBHOOK_TIMEOUT_SECONDS", 5, 1, 30)
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = _int_env("SMTP_PORT", 587, 1, 65_535)
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "process-monitor@localhost")
    smtp_to: str = os.getenv("SMTP_TO", "")
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    smtp_use_ssl: bool = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes", "on"}
    smtp_timeout_seconds: float = _float_env("SMTP_TIMEOUT_SECONDS", 10, 1, 60)
    version: str = os.getenv("APP_VERSION", "1.0.0")

    @property
    def auth_enabled(self) -> bool:
        """Authentication is enabled only when a non-empty token is configured."""
        return bool(self.auth_token and self.auth_token.strip())

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def allowed_origins(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a fresh settings object after environment variables are set."""
        return cls(
            database_path=Path(os.getenv("DB_PATH", "data/monitoring.db")),
            stale_after_seconds=_float_env("STALE_AFTER_SECONDS", 90, 15, 86_400),
            offline_after_seconds=_float_env("OFFLINE_AFTER_SECONDS", 900, 60, 604_800),
            default_window_seconds=_int_env("DEFAULT_WINDOW_SECONDS", 3_600, 60, 604_800),
            max_ingest_samples=_int_env("MAX_INGEST_SAMPLES", 500, 1, 10_000),
            max_request_bytes=_int_env("MAX_REQUEST_BYTES", 5_000_000, 100_000, 50_000_000),
            retention_days=_int_env("RETENTION_DAYS", 30, 1, 3_650),
            cors_origins=os.getenv("CORS_ORIGINS", "*"),
            auth_token=os.getenv("AUTH_TOKEN", ""),
            auth_protect_docs=os.getenv("AUTH_PROTECT_DOCS", "false").lower() in {"1", "true", "yes", "on"},
            webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
            webhook_timeout_seconds=_float_env("ALERT_WEBHOOK_TIMEOUT_SECONDS", 5, 1, 30),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=_int_env("SMTP_PORT", 587, 1, 65_535),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", "process-monitor@localhost"),
            smtp_to=os.getenv("SMTP_TO", ""),
            smtp_use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"},
            smtp_use_ssl=os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes", "on"},
            smtp_timeout_seconds=_float_env("SMTP_TIMEOUT_SECONDS", 10, 1, 60),
            version=os.getenv("APP_VERSION", "1.0.0"),
        )
