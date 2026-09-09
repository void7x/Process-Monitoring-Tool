"""Process Incident Analyzer — evidence-based reconstruction.

The analyzer uses **real telemetry only** from the existing SQLite store.
It never fabricates causal explanations; it summarizes what the data shows
leading up to an alert for a single process instance identified by
``host + pid + create_time``.

The default window is 60 seconds before the alert's ``triggered_at``
through the alert itself.  All calculations are deterministic and scoped
by the process-instance key so PID reuse cannot mix histories.
"""

from __future__ import annotations

import time
from typing import Any

from .db import Database

DEFAULT_INCIDENT_WINDOW_SECONDS = 60
MAX_TIMELINE_SAMPLES = 24


def _fmt_time(ts: float | None) -> str:
    if ts is None:
        return "—"
    try:
        # Use local time for the timeline, but keep it deterministic.
        # The frontend will re-format with toLocaleString; this is for
        # backend evidence strings and debugging.
        tm = time.localtime(float(ts))
        return time.strftime("%H:%M:%S", tm)
    except Exception:
        return str(ts)


def _metric_label(metric: str) -> str:
    return {
        "cpu_percent": "CPU",
        "memory_percent": "Memory",
        "memory_rss": "Memory RSS",
        "read_bytes": "Read I/O",
        "write_bytes": "Write I/O",
    }.get(metric, metric)


def _format_value(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric in ("cpu_percent", "memory_percent"):
        return f"{value:.1f}%"
    if metric in ("memory_rss", "read_bytes", "write_bytes"):
        # Human-readable bytes for evidence.
        n = float(value)
        if n < 1024:
            return f"{int(n)} B"
        for unit in ("KB", "MB", "GB"):
            n /= 1024
            if n < 1024:
                return f"{n:.1f} {unit}" if n < 10 else f"{int(n)} {unit}"
        return f"{n:.1f} GB"
    return f"{value:.1f}"


def _matches(value: float, operator: str, threshold: float) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
        "eq": value == threshold,
    }.get(operator, False)


class IncidentAnalyzer:
    """Compute an evidence-based incident analysis for a single alert."""

    def __init__(self, db: Database, window_seconds: int = DEFAULT_INCIDENT_WINDOW_SECONDS):
        self.db = db
        self.window_seconds = max(10, min(3600, int(window_seconds)))

    def analyze(self, alert_id: int, window_seconds: int | None = None) -> dict[str, Any] | None:
        """Return a structured incident analysis for ``alert_id`` or ``None`` if not found.

        The analysis is scoped strictly to ``host + pid + create_time`` and
        ``timestamp BETWEEN window_start AND window_end`` so unrelated
        processes (including PID reuse) are never scanned.
        """
        window = max(10, min(3600, int(window_seconds))) if window_seconds is not None else self.window_seconds

        alert = self.db.get_alert(alert_id)  # type: ignore[attr-defined]
        if alert is None:
            return None

        host = str(alert["host"])
        pid = int(alert["pid"])
        create_time = float(alert["create_time"])
        triggered_at = float(alert.get("triggered_at") or alert.get("last_seen") or time.time())
        metric = str(alert.get("metric") or "cpu_percent")
        operator = str(alert.get("operator") or "gt")
        threshold = float(alert.get("threshold") or 0)
        rule_id = str(alert.get("rule_id") or "")

        window_start = triggered_at - window
        window_end = triggered_at

        # Fetch history for this exact process instance in the window.
        # Use a generous limit but rely on the instance + time index.
        samples_desc, total = self.db.history(
            host=host, pid=pid, create_time=create_time, start=window_start, end=window_end, limit=1000, offset=0
        )
        # history returns DESC; we need ASC for timeline.
        samples = list(reversed(samples_desc))
        # Ensure strictly ordered by timestamp ascending, then id.
        samples.sort(key=lambda s: (float(s.get("timestamp") or 0), int(s.get("id") or 0)))

        # Also fetch the most recent sample outside window to know if history is truncated.
        # For lifecycle evidence we may want to know process start.
        sample_count = len(samples)
        insufficient = sample_count < 2

        # Current / previous / peak
        current_sample = samples[-1] if samples else None
        previous_sample = samples[-2] if len(samples) >= 2 else None

        # Helper to get metric value safely (None means unavailable)
        def _val(sample: dict | None, m: str) -> float | None:
            if sample is None:
                return None
            v = sample.get(m)
            if v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        current_value = _val(current_sample, metric)
        previous_value = _val(previous_sample, metric)
        # Peak in window for the alert metric
        peak_value: float | None = None
        peak_ts: float | None = None
        for s in samples:
            v = _val(s, metric)
            if v is None:
                continue
            if peak_value is None or v > peak_value:
                peak_value = v
                peak_ts = float(s.get("timestamp") or 0)

        delta: float | None = None
        if current_value is not None and previous_value is not None:
            delta = current_value - previous_value

        # Memory delta (RSS) if available — useful even when metric is CPU
        mem_current = _val(current_sample, "memory_rss")
        mem_previous = _val(previous_sample, "memory_rss")
        mem_delta: int | None = None
        if mem_current is not None and mem_previous is not None:
            try:
                mem_delta = int(mem_current - mem_previous)
            except Exception:
                mem_delta = None

        # Also compute memory_percent delta for evidence variant
        memp_current = _val(current_sample, "memory_percent")
        memp_previous = _val(previous_sample, "memory_percent")
        memp_delta: float | None = None
        if memp_current is not None and memp_previous is not None:
            memp_delta = memp_current - memp_previous

        # Duration above threshold: longest trailing suffix where condition holds
        duration_seconds: float | None = None
        if current_value is not None and samples:
            # Find the earliest index in the trailing run where metric satisfies condition
            # If current does not satisfy, duration is 0 (not above) -> None
            if _matches(current_value, operator, threshold):
                # Walk backwards to find first in run
                first_idx = len(samples) - 1
                for idx in range(len(samples) - 1, -1, -1):
                    v = _val(samples[idx], metric)
                    if v is None:
                        break
                    if not _matches(v, operator, threshold):
                        break
                    first_idx = idx
                # Duration is from first in run to current
                first_ts = float(samples[first_idx].get("timestamp") or triggered_at)
                current_ts = float(current_sample.get("timestamp") or triggered_at)  # type: ignore[union-attr]
                duration_seconds = max(0.0, current_ts - first_ts)
                # If only one sample in run, duration is 0
            else:
                duration_seconds = 0.0

        # Timeline — compact chronological list
        timeline: list[dict[str, Any]] = []
        # Only include up to MAX_TIMELINE_SAMPLES to keep payload small.
        # If many samples, sample evenly.
        display_samples = samples
        if len(samples) > MAX_TIMELINE_SAMPLES:
            step = len(samples) / MAX_TIMELINE_SAMPLES
            display_samples = [samples[int(i * step)] for i in range(MAX_TIMELINE_SAMPLES)]
            # Always include last sample
            if display_samples[-1] is not samples[-1]:
                display_samples[-1] = samples[-1]

        for s in display_samples:
            ts = float(s.get("timestamp") or 0)
            is_alert = current_sample is not None and ts == float(current_sample.get("timestamp") or 0)  # type: ignore[union-attr]
            # Process started if timestamp is within 2 seconds of create_time or is the earliest sample
            is_start = False
            if s is samples[0]:
                # Heuristic: if earliest sample's timestamp is close to create_time, it likely is start
                try:
                    if abs(ts - create_time) < 5:
                        is_start = True
                except Exception:
                    pass
            timeline.append(
                {
                    "timestamp": ts,
                    "time": _fmt_time(ts),
                    "cpu_percent": s.get("cpu_percent"),
                    "memory_percent": s.get("memory_percent"),
                    "memory_rss": s.get("memory_rss"),
                    "read_bytes": s.get("read_bytes"),
                    "write_bytes": s.get("write_bytes"),
                    "state": s.get("state"),
                    "is_alert": is_alert,
                    "is_start": is_start,
                    "label": "Alert triggered" if is_alert else ("Process started" if is_start else ""),
                }
            )
        # Ensure timeline ends with alert if not already (when triggered_at != last sample)
        # But only if we have at least one sample
        if samples and timeline and not any(e.get("is_alert") for e in timeline):
            # Append synthetic alert marker at triggered_at
            timeline.append(
                {
                    "timestamp": triggered_at,
                    "time": _fmt_time(triggered_at),
                    "cpu_percent": None,
                    "memory_percent": None,
                    "memory_rss": None,
                    "read_bytes": None,
                    "write_bytes": None,
                    "state": None,
                    "is_alert": True,
                    "is_start": False,
                    "label": "Alert triggered",
                }
            )
            timeline.sort(key=lambda e: float(e["timestamp"]))

        # Evidence — deterministic, real-data only
        evidence: list[str] = []
        if insufficient:
            evidence.append(f"Insufficient telemetry to determine the cause. Only {sample_count} sample(s) available in the {window} second window.")
            if sample_count == 1 and current_value is not None:
                evidence.append(f"Single observation for this process instance: {_metric_label(metric)} at {_format_value(metric, current_value)} at { _fmt_time(float(current_sample.get('timestamp') or triggered_at))}." if current_sample else "Single observation.")
            elif sample_count == 0:
                evidence.append("No samples were recorded for this process instance in the incident window.")
            # Still include what we can
            if current_value is not None:
                evidence.append(f"Current {_metric_label(metric)} was {_format_value(metric, current_value)} at alert time.")
        else:
            # Baseline / previous vs current
            if previous_value is not None and current_value is not None:
                if delta is not None and abs(delta) > 0.01:
                    direction = "increased" if delta > 0 else "decreased"
                    evidence.append(
                        f"{_metric_label(metric)} usage {direction} from {_format_value(metric, previous_value)} to {_format_value(metric, current_value)} before the alert (change of {delta:+.1f} points)."
                    )
                else:
                    evidence.append(
                        f"{_metric_label(metric)} remained at {_format_value(metric, current_value)} (previous {_format_value(metric, previous_value)})."
                    )
            elif current_value is not None:
                evidence.append(f"Current {_metric_label(metric)} was {_format_value(metric, current_value)} at alert time; previous reading was unavailable (None).")

            if peak_value is not None:
                # Avoid duplicate if peak == current and we already mentioned current
                if previous_value is not None and peak_value != current_value:
                    evidence.append(f"Peak {_metric_label(metric)} reached {_format_value(metric, peak_value)} at {_fmt_time(peak_ts)} during the incident window.")
                elif previous_value is None:
                    evidence.append(f"Peak {_metric_label(metric)} reached {_format_value(metric, peak_value)} during the incident window.")
                else:
                    # Still mention peak if it's the current value but distinct time
                    if peak_value == current_value and peak_ts is not None and current_sample is not None:
                        cur_ts = float(current_sample.get("timestamp") or 0)
                        if abs(peak_ts - cur_ts) > 1:
                            evidence.append(f"Peak {_metric_label(metric)} reached {_format_value(metric, peak_value)} at {_fmt_time(peak_ts)} during the incident window.")

            if duration_seconds is not None:
                if duration_seconds > 0:
                    evidence.append(
                        f"{_metric_label(metric)} remained above the configured threshold ({threshold:g} {operator}) for approximately {int(duration_seconds)} seconds before the alert."
                    )
                else:
                    # If duration 0, it just crossed threshold
                    if current_value is not None and _matches(current_value, operator, threshold):
                        evidence.append(f"{_metric_label(metric)} crossed the threshold ({threshold:g} {operator}) at the alert sample; no sustained duration was observed in the {window}s window.")

            if mem_delta is not None and abs(mem_delta) > 1024:
                direction = "increased" if mem_delta > 0 else "decreased"
                # Format as MB
                mb = mem_delta / (1024 * 1024)
                evidence.append(f"Memory (RSS) {direction} by {abs(mb):.1f} MB during the incident window (from {int(mem_previous)} to {int(mem_current)} bytes).")
            elif memp_delta is not None and abs(memp_delta) > 0.5:
                direction = "increased" if memp_delta > 0 else "decreased"
                evidence.append(f"Memory usage {direction} from {_format_value('memory_percent', memp_previous)} to {_format_value('memory_percent', memp_current)} during the same period.")

            # Lifecycle
            # Check if process started within window
            try:
                if window_start <= create_time <= window_end:
                    evidence.append(f"The process started at {_fmt_time(create_time)} within the incident window; only {int(triggered_at - create_time)} seconds of history were available before the alert.")
                elif samples:
                    earliest = float(samples[0].get("timestamp") or 0)
                    if earliest - create_time < 10:
                        evidence.append(f"Process started at {_fmt_time(create_time)}; incident window includes data from shortly after start.")
            except Exception:
                pass

            # State
            if current_sample:
                state_val = str(current_sample.get("state") or "")
                if state_val:
                    evidence.append(f"Process state at alert time was '{state_val}'.")

            if sample_count < window / 5:  # heuristic: sparse data
                # Don't claim insufficient if we already have good data, but note sparsity
                pass

            if not evidence:
                evidence.append("No clear change was observed in the available telemetry before the alert.")

        # Ensure evidence is deterministic and deduplicated
        # Keep order as generated, but remove exact duplicates
        seen_ev = set()
        deduped = []
        for e in evidence:
            if e not in seen_ev:
                seen_ev.add(e)
                deduped.append(e)
        evidence = deduped

        # Build summary — include metric identity so callers can render without
        # joining incident.  This mirrors the structured JSON contract expected
        # by the frontend and tests (incident/summary/timeline/evidence).
        summary: dict[str, Any] = {
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "window_seconds": window,
            "window_start": window_start,
            "window_end": window_end,
            "sample_count": sample_count,
            "total_samples_in_window": total,
            "previous_value": previous_value,
            "current_value": current_value,
            "peak_value": peak_value,
            "peak_timestamp": peak_ts,
            "delta": delta,
            "memory_delta_bytes": mem_delta,
            "duration_seconds": duration_seconds,
            "is_insufficient": insufficient,
            "previous_timestamp": float(previous_sample.get("timestamp") or 0) if previous_sample else None,
            "current_timestamp": float(current_sample.get("timestamp") or 0) if current_sample else triggered_at,
        }

        # Incident and process info
        incident = {
            "id": int(alert["id"]),
            "host": host,
            "pid": pid,
            "process_name": str(alert.get("process_name") or ""),
            "username": alert.get("username"),
            "create_time": create_time,
            "triggered_at": triggered_at,
            "last_seen": float(alert.get("last_seen") or triggered_at),
            "rule_id": rule_id,
            "rule_name": str(alert.get("rule_name") or ""),
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "severity": str(alert.get("severity") or "warning"),
            "status": str(alert.get("status") or "active"),
            "current_value": float(alert.get("current_value") or 0) if alert.get("current_value") is not None else None,
        }

        process = {
            "host": host,
            "pid": pid,
            "create_time": create_time,
            "process_name": str(alert.get("process_name") or ""),
            "username": alert.get("username"),
            "started_at": create_time,
            "started_time": _fmt_time(create_time),
            "age_seconds_at_alert": max(0.0, triggered_at - create_time) if triggered_at and create_time else None,
        }

        return {
            "incident": incident,
            "process": process,
            "summary": summary,
            "timeline": timeline,
            "evidence": evidence,
            "window": {"start": window_start, "end": window_end, "seconds": window},
        }
