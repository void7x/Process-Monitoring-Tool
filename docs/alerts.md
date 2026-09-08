# Alert rules

Rules are evaluated on newly accepted samples. State is persisted by `rule_id + host + pid + create_time`, so a reused PID cannot inherit a previous process's streak or active alert.

Fields:

- **Metric** — CPU percent, memory percent, memory RSS, read bytes, or write bytes.
- **Operator / threshold** — comparison and metric-unit value.
- **Duration** — how long the condition must remain true before triggering.
- **Consecutive samples** — number of matching samples required.
- **Cooldown** — quiet period after resolution to prevent flapping notifications.
- **Severity** — info, warning, or critical.
- **Action** — none, webhook, or email. Configure delivery only on the collector with environment variables.
- **Enabled** — whether future samples are evaluated.

A later non-matching sample for the same exact instance resolves an active alert. Alert history is never deleted when a rule is removed. Webhook/SMTP failures are logged and do not fail ingestion.

## Concurrency

Alert evaluation runs inside a single `BEGIN IMMEDIATE` transaction in
SQLite.  Two simultaneous ingestion requests for the same
`(rule_id, host, pid, create_time)` triple are serialized so they
cannot:

- create duplicate alerts for one process instance;
- corrupt the consecutive-sample counter;
- rewind the persisted alert streak with a buffered/older sample;
- race on the active-alert → resolved transition.

The `host + pid + create_time` key is also the unique constraint on
the `samples` table, so retried HTTP batches are idempotent and never
inflate sample counts.

## First-sample CPU

`cpu_percent` may be `null` on a process's very first sample because
psutil cannot produce a real CPU delta until it has a baseline.  The
alert engine skips CPU-based rules for samples with `null`
`cpu_percent` so a brand-new process does not falsely trigger (or
clear) a CPU alert on its first observation.  Real percentages arrive
from the second sample onward.

## Notification failure isolation

Webhook and email dispatch run on a background thread so a slow or
broken notification endpoint never blocks ingestion.  Failures are
logged with the alert ID and never include credentials.  The sample
and the alert state remain committed regardless of the notification
outcome.

## SMTP configuration

The collector honors these environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMTP_HOST` | *(empty)* | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (use 465 with `SMTP_USE_SSL=true`) |
| `SMTP_USERNAME` | *(empty)* | Optional SMTP username |
| `SMTP_PASSWORD` | *(empty)* | Optional SMTP password |
| `SMTP_USE_TLS` | `true` | Send `STARTTLS` after connecting |
| `SMTP_USE_SSL` | `false` | Use `SMTP_SSL` (implicit TLS) instead of plain SMTP |
| `SMTP_TIMEOUT_SECONDS` | `10` | Connection timeout |
| `SMTP_FROM` | `process-monitor@localhost` | Envelope From address |
| `SMTP_TO` | *(empty)* | Envelope To address |

Authenticated SMTP (`SMTP_USERNAME` + `SMTP_PASSWORD`) is supported
for both STARTTLS and implicit-SSL connections.  Failure modes (4xx,
5xx, DNS, timeout, STARTTLS, authentication) are caught and logged
so telemetry ingestion is never blocked by a broken mail server.

## Webhook configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALERT_WEBHOOK_URL` | *(empty)* | Endpoint that receives `POST` JSON payloads |
| `ALERT_WEBHOOK_TIMEOUT_SECONDS` | `5` | Request timeout |

Webhook bodies have the shape:

```json
{
  "event": "alert.triggered",
  "alert": { ... full alert record ... }
}
```

Failures (HTTP 4xx, HTTP 5xx, connection refused, timeout) are
caught and logged without rolling back the alert state.
