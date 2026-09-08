# Development guide

Keep external input in Pydantic models, SQL in `collector/db.py`, and process identity explicit in every new endpoint/query. Use `escapeHtml` for dynamic dashboard values and prefer data attributes plus delegated events over inline JavaScript handlers.

Recommended checks:

```bash
python -m compileall -q collector agent tests
python -m pytest -q
node --check ui/app.js
```

The frontend has no build step or CDN dependency. `ui/index.html`, `ui/app.js`, and `ui/styles.css` can be served by any static server; Compose adds nginx only for API proxying. Keep history and query results bounded, add indexes for new filters, and do not commit `.env`, databases, buffers, or notification credentials.
