"""Cross-platform psutil process sampler.

CPU measurement strategy
------------------------
psutil's ``Process.cpu_percent(interval=None)`` is a *delta* measurement: the
first call after a process appears (or after the process is constructed) has
no previous counter baseline, so it always returns ``0.0``.  Reporting that
``0.0`` as a real CPU reading on the first sample would:

* silently treat an unknown CPU usage as a legitimate "idle" reading, and
* prevent CPU-based alert rules from triggering on the first observation.

To stay responsive on Windows (and avoid blocking the whole agent with a
``cpu_percent(interval=...)`` style call for *every* process) we use a
two-phase approach:

1. **Prime phase** — the first time we see a process we record a baseline
   using ``cpu_percent(interval=None)`` and report ``cpu_percent=None`` for
   that sample.  ``None`` means "no reading yet" and is preserved through
   the agent → collector pipeline.
2. **Measurement phase** — on subsequent samples we call
   ``cpu_percent(interval=None)`` again.  psutil now has a baseline, so the
   returned value is the real delta over the interval since the prime.

The schema (Pydantic ``ProcessSample.cpu_percent``) already accepts ``0.0``
and above, so we relax it to also accept ``None`` and treat ``None`` as
"unavailable" in alert evaluation.  In one-shot mode (``--once``) the very
first cycle of a brand-new process therefore still reports ``None``; the
collector skips alert evaluation for that metric.  The next cycle (or the
next agent start) yields a real percentage.

Defensive handling is preserved for ``NoSuchProcess``, ``ZombieProcess``,
``AccessDenied``, ``PermissionError``, and ``OSError`` so a single protected
or short-lived process does not break the whole host sample.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import psutil

logger = logging.getLogger("process_monitor.agent.sampler")

# Sentinel used in ``samples`` for the first observation of a process whose
# CPU baseline has not yet been established.  ``None`` flows through the
# collector and is treated as "no reading" by alert evaluation and the
# dashboard.
CPU_BASELINE_PENDING: float | None = None


class ProcessSampler:
    """Collect bounded, defensive process samples.

    Access to a process can disappear between enumeration and inspection. Each
    process is handled independently so one protected/short-lived process does
    not interrupt the whole host sample.  CPU samples for newly discovered
    processes report ``None`` until a second observation establishes a real
    baseline percentage.
    """

    def __init__(self, hostname: str):
        self.hostname = hostname
        # ``pid -> create_time -> True`` remembers which exact process
        # instances have already had their CPU baseline primed.  The
        # ``host + pid + create_time`` triple is the project-wide instance
        # key, so a reused PID never inherits the old process baseline.
        self._cpu_primed: dict[tuple[int, float], bool] = {}

    def forget(self, pid: int | None = None) -> None:
        """Drop CPU baseline memory.

        Useful for tests and for an operator that wants the sampler to
        re-baseline every process.  With no arguments the whole cache is
        cleared.
        """
        if pid is None:
            self._cpu_primed.clear()
            return
        for key in [key for key in self._cpu_primed if key[0] == pid]:
            self._cpu_primed.pop(key, None)

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
                    pid = int(info["pid"])
                    instance_key = (pid, create_time)
                    io = info.get("io_counters")
                    memory_info = info.get("memory_info")
                    # Prime the per-process CPU counter on first sight.  The
                    # first call records the counter baseline and is
                    # discarded; the second call returns the real delta.
                    if instance_key not in self._cpu_primed:
                        try:
                            process.cpu_percent(interval=None)
                        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, PermissionError, OSError):
                            # The process may disappear between enumeration
                            # and priming.  Treat it as still un-primed and
                            # emit a sample without a CPU reading so the
                            # collector can record the rest of the row.
                            cpu_percent: float | None = None
                        else:
                            self._cpu_primed[instance_key] = True
                            cpu_percent = CPU_BASELINE_PENDING
                    else:
                        try:
                            # psutil returns a per-process percentage.  It
                            # may be above 100 on multi-core hosts and is
                            # kept as observed rather than silently
                            # normalized.
                            cpu_percent = max(0.0, float(process.cpu_percent(interval=None)))
                        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, PermissionError, OSError):
                            cpu_percent = None
                    samples.append(
                        {
                            "pid": pid,
                            "process_name": str(info.get("name") or "unknown"),
                            "username": info.get("username"),
                            "create_time": create_time,
                            "timestamp": timestamp,
                            "cpu_percent": cpu_percent,
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
