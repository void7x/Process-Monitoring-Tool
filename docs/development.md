# Development guide

Keep external input in Pydantic models, SQL in `collector/db.py`, and process identity explicit in every new endpoint/query. Use `escapeHtml` for dynamic dashboard values and prefer data attributes plus delegated events over inline JavaScript handlers.

Recommended checks:

```bash
python -m compileall -q collector agent tests
python -m pytest -q
node --check ui/app.js
node --test ui/tests/*.test.js    # frontend tests (requires `npm install`)
```

The shipped frontend has no build step or CDN dependency. `ui/index.html`, `ui/app.js`, and `ui/styles.css` can be served by any static server; Compose adds nginx only for API proxying.  Frontend tests use `jsdom` and live in `ui/tests/`; install dependencies once with `npm install` (the harness adds `jsdom` as a dev dependency only).  Keep history and query results bounded, add indexes for new filters, and do not commit `.env`, databases, buffers, or notification credentials.

## Adding a new API field

1. Add the column to the schema in `collector/db.py` and a non-breaking migration that defaults existing rows sensibly.
2. Update the matching Pydantic model in `collector/schemas.py`.
3. Add the field to the relevant `Database` query and to any response models.
4. Extend the test suite in `tests/test_api.py` / `tests/test_db.py`.
5. If the dashboard should display the field, update `ui/app.js` and the relevant `data-icon` set if needed.

## Adding a new alert metric

1. Add it to the `MetricName` literal in `collector/schemas.py` and to the agent's sample output.
2. Add the corresponding column to the `samples` table schema (or a new table if the metric needs its own retention policy).
3. Add a helper to `collector/db.py` and the alert engine `evaluate_sample` flow.
4. Add unit tests for the new metric, the rule evaluation path, and the dashboard renderer.
