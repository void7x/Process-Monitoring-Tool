# Process Incident Analyzer

The **Process Incident Analyzer** answers *“Why did this process become abnormal?”* using **real telemetry only** — it does not replace the monitoring, telemetry, alert, or dashboard systems and never fabricates a cause.

## Behavior

- When an alert triggers (e.g., `chrome.exe CPU > 80%`), the analyzer reconstructs the incident for that exact `host + pid + create_time` instance from the **60 seconds before the alert through the alert** (configurable `?window=10..3600`, default 60).
- If history is sparse it reports *“Insufficient telemetry”* rather than guessing.  It will never claim malware, user actions, or other unobserved causes.
- Evidence is a deterministic list derived from actual samples: previous value, peak, delta, duration above threshold, memory delta, lifecycle (started within window), and state at alert time.  Phrases such as *“CPU increased sharply”* are emitted only when the data shows it.

## API

```
GET /incidents/{alert_id}
GET /api/incidents/{alert_id}
GET /alerts/{alert_id}/incident
GET /api/alerts/{alert_id}/incident
?window=60   # optional, 10–3600 seconds
```

**Response** (structured JSON)

```json
{
  "incident": { "id": 42, "host": "web-01", "pid": 1234, "process_name": "chrome.exe", "metric": "cpu_percent", "operator": "gt", "threshold": 80, "triggered_at": 1700000050, "severity": "critical", ... },
  "process": { "host": "web-01", "pid": 1234, "create_time": 1700000000, "started_time": "12:00:00", "age_seconds_at_alert": 50 },
  "summary": { "metric": "cpu_percent", "previous_value": 45, "current_value": 95, "peak_value": 95, "delta": 50, "duration_seconds": 10, "window_seconds": 60, "sample_count": 5, "is_insufficient": false },
  "timeline": [ { "timestamp": 1700000000, "time": "12:00:00", "cpu_percent": 30, "memory_rss": 50000000, "is_start": true }, { "timestamp": 1700000050, "is_alert": true, "label": "Alert triggered" } ],
  "evidence": [ "Process chrome.exe (PID 1234) on web-01 breached cpu_percent threshold 80%: 95.0% at alert.", "CPU increased sharply (+50.0) before the alert.", ... ],
  "window": { "start": 1699999990, "end": 1700000050, "seconds": 60 }
}
```

- Returns **404** with `{ "error": { "code": "not_found", "message": "Incident was not found." } }` when the alert does not exist.
- Queries are **scoped** by `host + pid + create_time` and `timestamp BETWEEN window_start AND window_end` via `Database.history`; no full table scans.

## Frontend

- Each alert row shows a **“View Incident”** button (next to the process detail affordance).  Clicking it opens the **Process Incident Analyzer** drawer, which uses the current design language.
- The drawer handles four states: **loading** (“Analyzing incident…”), **success** (process / incident / key metrics / chronological timeline / evidence), **insufficient** (“Insufficient telemetry… Only N samples in the Ws window”), and **error** (“Incident analysis could not be loaded” with Retry).
- The timeline is ordered chronologically and is built from actual samples; evidence is never LLM-generated.

## Guarantees

- **No replacement** — consumes existing alerts without changing the alert engine, semantics, or notifications.
- **PID reuse safe** — the instance key `host + pid + create_time` is enforced in every query, so a reused PID does not mix histories.
- **No fabrication** — when data is insufficient the analyzer says *“Insufficient telemetry to determine the cause”* and still shows whatever timeline is available.
- **Bounded** — timeline is capped at 24 samples (evenly sampled if larger) and history fetches are limited to the window (max 1000 rows).
