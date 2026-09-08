# Troubleshooting

## Collector unavailable

Visit `/health`, inspect the collector log, and use the dashboard Retry button. The UI keeps the last successful update time and renders individual endpoint failures without crashing the whole page.

## No processes

Run `python -m agent.main --once`, verify `COLLECTOR_URL`, and check the agent log. A host agent in Docker cannot see a Windows host namespace; run it directly on Windows.

## Stale host

Freshness uses collector receipt time. Check network/firewall access and the agent JSONL buffer. Buffered batches are drained before new samples after recovery.

## Rule does not trigger

Check that the rule is enabled, the threshold uses the selected metric's unit, and duration/consecutive requirements are satisfied. Cooldown intentionally suppresses rapid re-triggering after resolution.

## Ports

Use another uvicorn port for local development and point the agent at it. Compose expects collector `8000` and dashboard `8080` unless mappings are changed.
