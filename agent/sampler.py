"""Cross-platform psutil process sampler."""
from __future__ import annotations

import logging
import time
from typing import Any

import psutil

logger = logging.getLogger("process_monitor.agent.sampler")


class ProcessSampler:
    """Collect bounded, defensive process samples.

    Access to a process can disappear between enumeration and inspection. Each
    process is handled independently so one protected/short-lived process does
    not interrupt the whole host sample.
    """

    def __init__(self, hostname: str):
        self.hostname = hostname

    def sample(self) -> list[dict[str, Any]]:
        timestamp = time.time()
        samples: list[dict[str, Any]] = []
        for process in psutil.process_iter(
            ["pid", "name", "username", "create_time", "memory_percent", "memory_info", "io_counters", "status"]
        ):
            try:
                with process.oneshot():
                    info = process.info
                    create_time = float(info.get("create_time") or 0)
                    if create_time <= 0:
                        continue
                    io = info.get("io_counters")
                    memory_info = info.get("memory_info")
                    samples.append(
                        {
                            "pid": int(info["pid"]),
                            "process_name": str(info.get("name") or "unknown"),
                            "username": info.get("username"),
                            "create_time": create_time,
                            "timestamp": timestamp,
                            # psutil returns a per-process percentage. It may
                            # be above 100 on multi-core hosts and is kept as
                            # observed rather than silently normalized.
                            "cpu_percent": max(0.0, float(process.cpu_percent(interval=None))),
                            "memory_percent": max(0.0, min(100.0, float(info.get("memory_percent") or 0))),
                            "memory_rss": max(0, int(getattr(memory_info, "rss", 0) or 0)),
                            "read_bytes": max(0, int(getattr(io, "read_bytes", 0) or 0)),
                            "write_bytes": max(0, int(getattr(io, "write_bytes", 0) or 0)),
                            "state": str(info.get("status") or "running"),
                        }
                    )
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, PermissionError, OSError):
                continue
            except Exception:
                logger.exception("Unexpected process sampling error")
        return samples
