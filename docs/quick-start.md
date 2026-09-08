# Quick start

## Local collector and dashboard

```bash
python -m venv .venv
source .venv/bin/activate                 # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn collector.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The collector serves the static dashboard locally and exposes OpenAPI at `/docs`.

In another terminal:

```bash
python -m agent.main --once
python -m agent.main
```

The agent defaults to `http://127.0.0.1:8000`. Use `COLLECTOR_URL` and `AGENT_HOSTNAME` to target another collector/identity.

## Verify

```bash
python -m compileall -q collector agent tests
python -m pytest -q
node --check ui/app.js
```

For Compose, see [Docker](docker.md). For a Windows host agent, see [Windows](windows.md).
