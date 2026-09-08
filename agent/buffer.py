"""Bounded JSONL spool for samples collected during outages."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("process_monitor.agent.buffer")


class LocalBuffer:
    def __init__(self, path: str | Path, max_batches: int = 200):
        self.path = Path(path)
        self.max_batches = max_batches
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict) and "host" in record and "samples" in record:
                            records.append(record)
                    except json.JSONDecodeError:
                        logger.warning("Ignoring malformed local buffer line")
        except OSError:
            logger.exception("Could not read local buffer")
        return records[-self.max_batches :]

    def replace(self, records: list[dict[str, Any]]) -> None:
        records = records[-self.max_batches :]
        if not records:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not clear local buffer")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, temporary = tempfile.mkstemp(prefix="agent-buffer-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                for record in records:
                    file.write(json.dumps(record, separators=(",", ":")) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            logger.exception("Could not write local buffer")
            try:
                os.unlink(temporary)
            except (OSError, UnboundLocalError):
                pass

    def append(self, payload: dict[str, Any]) -> None:
        self.replace([*self.read(), payload])

    def pending_count(self) -> int:
        return len(self.read())
