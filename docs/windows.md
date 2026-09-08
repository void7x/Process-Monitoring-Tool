# Windows host setup

Install Python 3.11 or newer, then run the agent directly on the Windows host when host-process visibility matters:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:COLLECTOR_URL = "http://127.0.0.1:8000"
$env:AGENT_HOSTNAME = $env:COMPUTERNAME
python -m agent.main
```

A Docker container does not automatically share the Windows host process namespace. It is fine to run the collector/dashboard in Compose and the agent directly on Windows. The agent handles access-denied and short-lived processes, retries collector requests, and keeps a bounded JSONL spool while the collector is unavailable.

To run one cycle for a smoke check:

```powershell
python -m agent.main --once
```
