# Docker setup

Start the collector and dashboard:

```bash
docker compose up --build
```

- Dashboard: `http://localhost:8080`
- Collector/OpenAPI: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

The collector stores SQLite data in the named `collector-data` volume. The dashboard nginx service proxies `/api/*` to the collector service; the browser never calls a container-local hostname.

```bash
docker compose down       # keeps the named volume
# docker compose down -v   # deletes monitoring history too
```

For Windows host monitoring, run `python -m agent.main` on Windows rather than placing the agent inside the Linux collector container.
