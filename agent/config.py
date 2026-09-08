"""Environment-driven agent configuration."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class AgentSettings:
    collector_url: str = os.getenv("COLLECTOR_URL", "http://127.0.0.1:8000")
    hostname: str = os.getenv("AGENT_HOSTNAME", socket.gethostname())
    sample_interval_seconds: float = _float("SAMPLE_INTERVAL_SECONDS", 5, 0.5, 3_600)
    batch_size: int = _int("AGENT_BATCH_SIZE", 100, 1, 500)
    buffer_file: Path = Path(os.getenv("AGENT_BUFFER_FILE", "data/agent-buffer.jsonl"))
    max_buffer_batches: int = _int("AGENT_MAX_BUFFER_BATCHES", 200, 1, 10_000)
    request_timeout_seconds: float = _float("AGENT_REQUEST_TIMEOUT_SECONDS", 5, 1, 60)
    retry_attempts: int = _int("AGENT_RETRY_ATTEMPTS", 3, 1, 8)
    auth_token: str = os.getenv("AUTH_TOKEN", os.getenv("AGENT_AUTH_TOKEN", ""))

    @classmethod
    def from_env(cls) -> "AgentSettings":
        return cls(
            collector_url=os.getenv("COLLECTOR_URL", "http://127.0.0.1:8000").rstrip("/"),
            hostname=os.getenv("AGENT_HOSTNAME", socket.gethostname()),
            sample_interval_seconds=_float("SAMPLE_INTERVAL_SECONDS", 5, 0.5, 3_600),
            batch_size=_int("AGENT_BATCH_SIZE", 100, 1, 500),
            buffer_file=Path(os.getenv("AGENT_BUFFER_FILE", "data/agent-buffer.jsonl")),
            max_buffer_batches=_int("AGENT_MAX_BUFFER_BATCHES", 200, 1, 10_000),
            request_timeout_seconds=_float("AGENT_REQUEST_TIMEOUT_SECONDS", 5, 1, 60),
            retry_attempts=_int("AGENT_RETRY_ATTEMPTS", 3, 1, 8),
            auth_token=os.getenv("AUTH_TOKEN", os.getenv("AGENT_AUTH_TOKEN", "")),
        )
