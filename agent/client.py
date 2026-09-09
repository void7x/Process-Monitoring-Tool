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
    def __init__(self, base_url: str, timeout: float = 5, retry_attempts: int = 3, auth_token: str = ""):
        self.url = base_url.rstrip("/") + "/ingest"
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        # ``auth_token`` is optional.  When configured on the collector, the
        # agent must present it as a Bearer credential or the collector will
        # reject the request with 401.  An empty token disables auth headers
        # so the agent works against a default local collector.
        self.auth_token = auth_token.strip() if auth_token else ""

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def send(self, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        for attempt in range(self.retry_attempts):
            request = urllib.request.Request(
                self.url,
                data=body,
                headers=self._build_headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured collector
                    if 200 <= response.status < 300:
                        return True
                    logger.warning("Collector rejected samples", extra={"status": response.status})
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    # Auth is a configuration issue, not a transient failure.
                    logger.warning("Collector rejected request as unauthenticated; check AUTH_TOKEN")
                    return False
                if attempt == self.retry_attempts - 1:
                    logger.warning("Collector rejected samples", extra={"status": exc.code})
                else:
                    delay = min(30.0, 0.5 * (2**attempt))
                    logger.info("Collector request failed; retrying", extra={"delay": delay, "status": exc.code})
                    time.sleep(delay)
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
