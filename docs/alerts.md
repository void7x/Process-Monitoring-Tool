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
