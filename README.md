# Process Monitor

Process Monitor is a lightweight observability console for Windows and Linux process workloads. A small `psutil` agent samples local processes, the FastAPI collector stores durable SQLite history and evaluates alert rules, and a responsive light-theme dashboard makes the data easy to operate.

The project is intentionally dependency-light: the UI is a static, dependency-free SPA with inline SVG chart rendering, so the dashboard works in a disconnected or restricted environment as well as in Docker.

## Documentation

Detailed guides live in [`docs/`](docs/index.md): quick start, Windows and Docker setup, API, alerts, architecture, development, and troubleshooting.

## What is included

- **Agent** — Windows/Linux process sampling with `psutil`, graceful shutdown, retry/backoff, a bounded JSONL outage spool, and a pruned per-instance CPU baseline cache that stays bounded even with many short-lived processes.
- **Collector** — FastAPI service, SQLite WAL persistence, typed request validation, health/readiness endpoints, structured error responses, host freshness, history, rules, alert state, deterministic post-commit webhook/email dispatch, and optional authentication.
- **Dashboard** — Overview, hosts, process explorer, process-instance detail drawer, alerts, rules CRUD, history, and settings with loading/empty/error/offline states, a professional light theme with clear hierarchy, and built-in auth-token management.
- **Incident Analyzer** — evidence-based reconstruction for a single alert (`host + pid + create_time` in the 60 s window before the alert) with previous/peak/delta/duration calculations, a chronological timeline from actual samples, and deterministic evidence such as “CPU increased sharply” or “Insufficient telemetry” — never fabricates causes and is scoped by instance + timestamp range (see `collector/incidents.py` and `docs/incident-analyzer.md`).
- **Tests** — API, database identity/idempotency, alert duration/consecutive/cooldown behavior, PID reuse isolation, agent buffering/sampling and CPU-cache pruning, deterministic notification ordering, incident history/PID-reuse/insufficient/fabrication/API/frontend interaction coverage, and frontend auth-header coverage.

## Important correctness guarantees

The sample identity is always:

```text
host + pid + create_time + timestamp
```

`create_time` is part of the process-instance key everywhere: the SQLite unique constraint, latest-process query, alert state, active alerts, process detail, and historical series. If a process exits and its PID is reused, the new process cannot inherit the old process history or alert streak. Re-sending the same sample is safe because ingestion uses an idempotent unique constraint.

Historical samples are retained independently of host freshness. A host can become stale or offline without deleting its process history.

### Second-pass hardening (CPU cache, deterministic notifications, auth UI, professional theme)

- **CPU baseline cache pruning** — the agent tracks a `(pid, create_time)` → last-seen map and prunes entries not seen in the current cycle.  When the table would exceed its bound (default 5000 entries) the oldest entries are evicted first.  This keeps memory bounded on hosts with heavy churn while preserving the `None` on first-sample semantics and PID-reuse isolation.
- **Deterministic post-commit dispatch** — `POST /ingest` evaluates all rules with `notify=False`, commits the `BEGIN IMMEDIATE` alert transaction, and then dispatches the collected alerts once, sorted by `(triggered_at, rule_id, host, pid, create_time)`.  Notifications are therefore post-commit, never block ingestion, and produce a stable observable order even under concurrent batches.
- **Frontend auth token support** — when `AUTH_TOKEN` is configured the dashboard reads the token from `localStorage` (or `window.MONITORING_CONFIG.authToken`), sends it as both `Authorization: Bearer …` and `X-Auth-Token` on every API request, surfaces 401 failures with an inline hint in Settings, and provides Save / Clear / Test actions in the Settings page.  The token is never logged or added to URLs.
- **Professional UI redesign** — the dashboard now uses refined design tokens, layered shadows, improved typography and spacing, clearer KPI and host cards, more readable data tables, and a polished empty/error state.  All behavioral selectors (`.kpi-card.tone-*`, `.alert-row.alert-*`, etc.) are preserved so existing tests remain valid.

### First-sample CPU behavior

`psutil.Process.cpu_percent(interval=None)` is a *delta* measurement: the
first call after a process appears has no previous baseline and would
otherwise return `0.0`.  The agent therefore emits `cpu_percent: null`
on the first sample of every `host + pid + create_time` instance and a
real percentage on every subsequent sample.  The collector stores
`NULL` for that field, and the alert engine skips CPU-based rules
when the reading is unavailable.  This avoids creating or resolving an
alert based on a phantom "idle" reading for a brand-new process.

### Running Processes KPI

The `total_running_processes` KPI in `/summary` is the count of
*latest* process instances whose `state` field is exactly `running`.
A process that is `sleeping`, `stopped`, `idle`, or whose state is
unknown is **not** counted as running, even if a sample was received
recently.  This is the operational definition of "running" and is
the value the dashboard reports.

## Quick start: local development

Python 3.11+ is recommended.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1

python -m pip install -r requirements-dev.txt
uvicorn collector.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>. The collector serves the dashboard directly for local development. API documentation is available at <http://localhost:8000/docs>.

In a second terminal, with the virtual environment active:

```bash
python -m agent.main --once       # one collection cycle
python -m agent.main              # continuous collection
```

The default agent target is `http://127.0.0.1:8000`. Set `COLLECTOR_URL` when the collector is remote. Runtime configuration can be copied from `.env.example`; the application reads environment variables directly and does not require `python-dotenv`.

Run checks:

```bash
python -m compileall -q collector agent tests
python -m pytest -q
node --check ui/app.js
node --test ui/tests/*.test.js   # frontend tests
```

## Docker Compose

Docker is useful for the collector and dashboard. The agent should generally run on the host whose process namespace it needs to observe.

```bash
docker compose up --build
```

- Dashboard: <http://localhost:8080>
- Collector API: <http://localhost:8000>
- OpenAPI docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

SQLite data is stored in the named `collector-data` volume. Stop without removing the volume to preserve history:

```bash
docker compose down
```

## Windows host workflow

A container does not automatically see the Windows host process namespace. For host-level Windows visibility, run the agent directly on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:COLLECTOR_URL = "http://127.0.0.1:8000"
$env:AGENT_HOSTNAME = $env:COMPUTERNAME
python -m agent.main
```

If the collector is running on another machine, replace `COLLECTOR_URL` with its reachable address and configure the collector firewall/CORS appropriately. The agent catches `AccessDenied`, short-lived processes, and process disappearance between enumeration and inspection.

## API overview

The same API is available at the documented root paths and under `/api/*` aliases for the dashboard and reverse proxy.

### System and ingestion

- `GET /health` — liveness and database check.
- `GET /ready` — readiness check.
- `POST /ingest` — validate and idempotently ingest a bounded batch.

Example payload:

```json
{
  "host": "workstation-01",
  "samples": [
    {
      "pid": 1234,
      "process_name": "example.exe",
      "username": "operator",
      "create_time": 1710000000.25,
      "timestamp": 1710000010.25,
      "cpu_percent": 14.2,
      "memory_percent": 1.1,
      "memory_rss": 52428800,
      "read_bytes": 2048,
      "write_bytes": 4096,
      "state": "running"
    }
  ]
}
```

### Dashboard data

- `GET /summary?window=3600` — KPI values and host health.
- `GET /metrics/summary?window=3600&host=...` — CPU, memory, sample activity, and alert time series.
- `GET /hosts` and `GET /hosts/{host}` — host list and detail.
- `GET /processes` — paginated latest process instances; supports `search`, `host`, `cpu_min`, `memory_min`, `alert_only`, `sort`, and `order`.
- `GET /processes/{host}/{pid}?create_time=...` — exact process-instance detail, history, charts, and alerts. Always pass `create_time` when investigating a known instance.
- `GET /history` — retained samples with host/PID/create-time/time-window filters.
- `GET /metrics` — compatibility history query alias.
- `GET /alerts`, `GET /alerts/summary` — active/resolved alert data and counts. `/alerts` supports `status`, `search`, `severity`, `host`, and `since` timestamp filters.

### Rule management

- `GET /alerts/rules`
- `GET /alerts/rules/{rule_id}`
- `POST /alerts/rules`
- `PUT` or `PATCH /alerts/rules/{rule_id}`
- `DELETE /alerts/rules/{rule_id}`

### Incident Analyzer

- `GET /incidents/{alert_id}` (also `/api/incidents/{alert_id}` and `/alerts/{alert_id}/incident`) — evidence-based reconstruction for the exact `host + pid + create_time` instance over the 60 s window before the alert (configurable `?window=10..3600`).  Returns `{ incident, process, summary, timeline, evidence, window }` where `summary` includes previous/peak/delta/duration and `timeline` is chronological from real samples; when `summary.is_insufficient` is true the `evidence` reports “Insufficient telemetry” and never fabricates a cause.  Scoped queries use `host + pid + create_time + timestamp BETWEEN window_start AND window_end` via `Database.history`; no full DB scans.  Dashboard: each alert row exposes a “View Incident” affordance that opens a panel/modal with loading/empty/error/real-data states using the existing design language.  See `docs/incident-analyzer.md`.

Rule fields:

- `metric`: `cpu_percent`, `memory_percent`, `memory_rss`, `read_bytes`, or `write_bytes`.
- `operator`: `gt`, `gte`, `lt`, `lte`, or `eq`.
- `threshold`: compared against the observed metric unit.
- `duration`: seconds the condition must remain true.
- `consecutive_samples`: number of matching samples required.
- `cooldown`: quiet period after resolution.
- `action`: `none`, `webhook`, or `email`.
- `severity`: `info`, `warning`, or `critical`.
- `enabled`: whether new samples are evaluated.

Alert state is persisted and keyed by `rule_id + host + pid + create_time`. Active alerts resolve when a later sample for that exact process instance clears the condition. Optional delivery is configured server-side with `ALERT_WEBHOOK_URL` or SMTP variables; missing delivery configuration never makes ingestion fail. Delivery is dispatched post-commit in a deterministic sorted order (see `docs/alerts.md`). The dashboard includes first-class auth-token handling: the token is stored only in browser `localStorage`, sent on every non-health request, and manageable from Settings.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_PATH` | `data/monitoring.db` | SQLite database path |
| `STALE_AFTER_SECONDS` | `90` | Host freshness threshold |
| `OFFLINE_AFTER_SECONDS` | `900` | When a stale host is shown offline |
| `DEFAULT_WINDOW_SECONDS` | `3600` | Default chart/query window |
| `MAX_INGEST_SAMPLES` | `500` | Maximum samples per request |
| `MAX_REQUEST_BYTES` | `5000000` | Request size guard |
| `RETENTION_DAYS` | `30` | Sample cleanup horizon for operators that call pruning |
| `CORS_ORIGINS` | `*` | Comma-separated allowed dashboard origins |
| `AUTH_TOKEN` | *(empty)* | Optional bearer token. When set, the agent and dashboard must present `Authorization: Bearer <token>` (or `X-Auth-Token`) for every non-health request. The dashboard stores the token in `localStorage` under `process-monitor-auth-token` and manages it from Settings (Save/Clear/Test), sending both headers on each request. |
| `AUTH_PROTECT_DOCS` | `false` | When `true`, `/docs` and `/openapi.json` also require the token. |
| `ALERT_WEBHOOK_URL` | *(empty)* | Optional webhook endpoint for `action: webhook` rules. |
| `ALERT_WEBHOOK_TIMEOUT_SECONDS` | `5` | Webhook request timeout. |
| `SMTP_HOST` | *(empty)* | Optional SMTP host for `action: email` rules. |
| `SMTP_PORT` | `587` | SMTP port. Use 465 with `SMTP_USE_SSL=true` for implicit TLS. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | Optional SMTP credentials. |
| `SMTP_USE_TLS` | `true` | Send `STARTTLS` after connecting. |
| `SMTP_USE_SSL` | `false` | Use `SMTP_SSL` (implicit TLS) instead of plain `SMTP`. |
| `SMTP_TIMEOUT_SECONDS` | `10` | SMTP connection timeout. |
| `SMTP_FROM` | `process-monitor@localhost` | Envelope From address. |
| `SMTP_TO` | *(empty)* | Envelope To address. |
| `COLLECTOR_URL` | `http://127.0.0.1:8000` | Agent ingestion endpoint base |
| `SAMPLE_INTERVAL_SECONDS` | `5` | Agent sampling interval |
| `AGENT_BATCH_SIZE` | `100` | Agent batch size |
| `AGENT_BUFFER_FILE` | `data/agent-buffer.jsonl` | Bounded outage spool |
| `AGENT_MAX_BUFFER_BATCHES` | `200` | Spool limit |

## Architecture

```text
psutil agent(s)
   │ HTTP JSON batches with retry/backoff
   ▼
FastAPI collector ── AlertEngine ── optional webhook/SMTP dispatcher
   │
   ▼
SQLite WAL (samples, hosts, rules, alert states, alerts)
   │
   ├── /summary, /hosts, /processes, /history, /alerts, /metrics
   └── static dashboard locally / nginx reverse proxy in Compose
```

The repository layer in `collector/db.py` owns SQL, indexes, freshness queries, pagination, retention-safe history, and process-instance isolation. The UI uses relative `/api` requests so it works behind the Compose nginx proxy and never calls a browser-inaccessible `localhost` backend.

## Troubleshooting

**The dashboard says Collector unavailable**

1. Visit `/health` on port 8000. If it returns 401, the collector requires `AUTH_TOKEN`.
2. Check that the collector process/container is running.
3. If the dashboard is on another origin, set `CORS_ORIGINS` to that exact origin.
4. Use the dashboard Retry button; it retains the last successful update timestamp.
5. When auth is enabled, open Settings → Authentication, paste the bearer token, Save, and Test connection. The token is sent as `Authorization: Bearer` and `X-Auth-Token` and never appears in URLs or logs.

**No processes are visible**

Start the agent with the correct `COLLECTOR_URL`, run `python -m agent.main --once`, and inspect the agent log. In Docker, run the agent outside the container when it must see Windows host processes.

**A host is stale**

The collector uses receipt time for freshness, not only the agent's sample clock. Check connectivity, firewall rules, and the local JSONL buffer. Buffered batches are drained before new samples after recovery.

**Alerts do not trigger**

Check that the rule is enabled, the metric unit matches the threshold, and both duration and consecutive-sample requirements are satisfied. A cooldown deliberately suppresses rapid flapping after resolution.

**Port already in use**

Run uvicorn on another port and set `COLLECTOR_URL` for the agent, or change the Compose port mapping. The dashboard's nginx proxy still targets the service name inside the Compose network.

## Development guide

Keep SQL in the repository layer, validate all external input with Pydantic, preserve `host + pid + create_time` in new process APIs, and use bounded pagination/history. The frontend is intentionally modularized around rendering helpers, API requests, stateful filters, accessible buttons, and safe `escapeHtml` rendering. If SSE/WebSockets are added later, replace the refresh scheduler with an event adapter rather than coupling transport logic to components.

Before submitting a change:

```bash
python -m compileall -q collector agent tests
python -m pytest -q
node --check ui/app.js
```

Do not commit `.env`, database files, local agent buffers, or notification credentials.
