"""HTTP client with bounded retry/backoff for agent ingestion."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("process_monitor.agent.client")


class CollectorClient:
    def __init__(self, base_url: str, timeout: float = 5, retry_attempts: int = 3):
        self.url = base_url.rstrip("/") + "/ingest"
        self.timeout = timeout
        self.retry_attempts = retry_attempts

    def send(self, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        for attempt in range(self.retry_attempts):
            request = urllib.request.Request(
                self.url,
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured collector
                    if 200 <= response.status < 300:
                        return True
                    logger.warning("Collector rejected samples", extra={"status": response.status})
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retry_attempts - 1:
                    logger.warning("Collector unavailable after retries", extra={"error": str(exc)})
                else:
                    delay = min(30.0, 0.5 * (2**attempt))
                    logger.info("Collector request failed; retrying", extra={"delay": delay, "error": str(exc)})
                    time.sleep(delay)
            except Exception:
                logger.exception("Unexpected collector client error")
                break
        return False
