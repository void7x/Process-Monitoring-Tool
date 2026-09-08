# Architecture

```text
psutil agent(s)
    │ JSON HTTP batches, retries, bounded outage spool
    ▼
FastAPI collector ── AlertEngine ── optional webhook/SMTP dispatcher
    │
    ▼
SQLite WAL repository
    │
    ├── samples / hosts / rules / alert_states / alerts
    └── summary, metrics, hosts, processes, history, alerts APIs
                     │
                     ▼
           static responsive dashboard
```

`collector/db.py` is the repository boundary for all SQL. It owns additive schema setup/migration, indexes, idempotent ingestion, snapshot queries, pagination, time-window aggregation, and instance-aware history. `collector/alerts.py` owns comparisons, duration/consecutive/cooldown state, exact-instance resolution, and best-effort notifications.

The UI uses relative `/api` URLs and independent panel requests. Polling is isolated behind a refresh scheduler so an SSE/WebSocket adapter can be added without changing page components.
