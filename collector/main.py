"""FastAPI application for the process monitoring collector."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .alerts import AlertEngine
from .auth import install_auth
from .config import Settings
from .db import Database
from .schemas import HealthResponse, IngestRequest, IngestResponse, RuleCreate, RuleListResponse, RuleResponse, RuleUpdate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("process_monitor.collector")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"error": {"code": "request_too_large", "message": "Request body exceeds the configured limit."}},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "invalid_content_length", "message": "Invalid Content-Length header."}},
                )
        return await call_next(request)


def _error(code: str, message: str, *, status_code: int = 400, details: Any | None = None) -> HTTPException:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(status_code=status_code, detail=payload)


def _window(value: int | None, default: int, maximum: int = 604_800) -> int:
    if value is None:
        return default
    return max(60, min(maximum, value))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.initialize()
    engine = AlertEngine(db, settings)

    async def retention_loop() -> None:
        while True:
            await asyncio.sleep(6 * 60 * 60)
            try:
                deleted = db.prune_samples(settings.retention_days)
                logger.info("Retention pass complete", extra={"deleted_samples": deleted})
            except Exception:
                logger.exception("Retention pass failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("Collector starting", extra={"database": str(settings.database_path), "version": settings.version})
        try:
            deleted = db.prune_samples(settings.retention_days)
            logger.info("Startup retention pass complete", extra={"deleted_samples": deleted})
        except Exception:
            logger.exception("Startup retention pass failed")
        task = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            logger.info("Collector stopped")

    app = FastAPI(
        title="Process Monitor Collector",
        version=settings.version,
        description=(
            "A lightweight, instance-safe process monitoring collector. "
            "Sample identity is host + PID + create_time + timestamp."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.alert_engine = engine
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=settings.allowed_origins != ["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*", "Authorization", "X-Auth-Token"],
    )
    install_auth(app, settings)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "The request did not pass validation.",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            payload = detail
        else:
            payload = {"code": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": payload}, headers=exc.headers)

    @app.get("/health", tags=["system"], response_model=HealthResponse)
    @app.get("/api/health", tags=["system"], include_in_schema=False, response_model=HealthResponse)
    async def health() -> dict[str, Any]:
        try:
            with db.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return {"status": "ok", "service": "collector", "version": settings.version, "time": time.time()}
        except Exception as exc:  # pragma: no cover - defensive operational path
            logger.exception("Health check failed")
            raise _error("database_unavailable", "Collector database is unavailable.", status_code=503) from exc

    @app.get("/ready", tags=["system"])
    @app.get("/api/ready", tags=["system"], include_in_schema=False)
    async def ready() -> dict[str, Any]:
        return await health()

    @app.post("/ingest", tags=["ingestion"], response_model=IngestResponse)
    @app.post("/api/ingest", tags=["ingestion"], include_in_schema=False, response_model=IngestResponse)
    async def ingest(payload: IngestRequest) -> dict[str, Any]:
        if len(payload.samples) > settings.max_ingest_samples:
            raise _error(
                "batch_too_large",
                f"A batch may contain at most {settings.max_ingest_samples} samples.",
                status_code=413,
            )
        result = db.insert_samples(payload.host, [sample.model_dump() for sample in payload.samples])
        newly_triggered: list[dict[str, Any]] = []
        for sample in result["inserted"]:
            try:
                # ``notify=False`` defers notification until after the batch
                # has been committed.  This makes the ingestion transaction
                # small and allows the dispatcher to emit alerts in a
                # deterministic, sorted order once durability is guaranteed.
                newly_triggered.extend(engine.evaluate_sample(sample, notify=False))
            except Exception:
                # The sample is durable even if a malformed legacy rule or an
                # optional dispatcher has a problem. Log the incident and keep
                # the agent's ingestion contract reliable.
                logger.exception("Alert evaluation failed", extra={"host": payload.host, "pid": sample.get("pid")})
        # Post-commit, deterministic dispatch.  All alert state has been
        # committed via ``evaluate_alert_atomic``; notifications now run in a
        # stable sorted order (triggered_at, rule_id, host, pid,
        # create_time) so concurrent batches produce the same observable
        # delivery sequence and retries never cause duplicate commits.
        if newly_triggered:
            try:
                engine.dispatch_triggered_batch(newly_triggered)
            except Exception:
                logger.exception("Post-commit alert dispatch failed", extra={"count": len(newly_triggered)})
        logger.info(
            "Samples ingested",
            extra={"host": payload.host, "accepted": result["accepted"], "duplicates": result["duplicates"]},
        )
        return {
            "status": "accepted",
            "host": payload.host,
            "accepted": result["accepted"],
            "duplicates": result["duplicates"],
            "alerts_triggered": len(newly_triggered),
            "received_at": time.time(),
        }

    @app.get("/summary", tags=["dashboard"])
    @app.get("/api/summary", tags=["dashboard"], include_in_schema=False)
    async def summary(window: int | None = Query(default=None, ge=60, le=604_800)) -> dict[str, Any]:
        return db.summary(
            stale_after=settings.stale_after_seconds,
            offline_after=settings.offline_after_seconds,
            lookback_seconds=_window(window, settings.default_window_seconds),
        )

    @app.get("/metrics/summary", tags=["metrics"])
    @app.get("/api/metrics/summary", tags=["metrics"], include_in_schema=False)
    async def metrics_summary(
        host: str | None = None,
        window: int | None = Query(default=None, ge=60, le=604_800),
    ) -> dict[str, Any]:
        return db.metrics_summary(host=host, lookback_seconds=_window(window, settings.default_window_seconds))

    @app.get("/metrics", tags=["metrics"])
    @app.get("/api/metrics", tags=["metrics"], include_in_schema=False)
    async def metrics(
        host: str | None = None,
        pid: int | None = Query(default=None, ge=0),
        create_time: float | None = Query(default=None, gt=0),
        start: float | None = Query(default=None, gt=0),
        end: float | None = Query(default=None, gt=0),
        limit: int = Query(default=500, ge=1, le=5_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = db.history(host=host, pid=pid, create_time=create_time, start=start, end=end, limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/hosts", tags=["hosts"])
    @app.get("/api/hosts", tags=["hosts"], include_in_schema=False)
    async def hosts(search: str = Query(default="", max_length=255), window: int | None = Query(default=None, ge=60, le=604_800)) -> dict[str, Any]:
        items = db.hosts_with_alert_counts(
            stale_after=settings.stale_after_seconds,
            offline_after=settings.offline_after_seconds,
            lookback_seconds=_window(window, max(settings.default_window_seconds, int(settings.offline_after_seconds))),
            now=time.time(),
        )
        if search:
            needle = search.casefold()
            items = [item for item in items if needle in item["host"].casefold()]
        return {"items": items, "total": len(items)}

    @app.get("/hosts/{host}", tags=["hosts"])
    @app.get("/api/hosts/{host}", tags=["hosts"], include_in_schema=False)
    async def host_detail(host: str, window: int | None = Query(default=None, ge=60, le=604_800)) -> dict[str, Any]:
        result = db.get_host(
            host,
            stale_after=settings.stale_after_seconds,
            offline_after=settings.offline_after_seconds,
            lookback_seconds=_window(window, max(settings.default_window_seconds, int(settings.offline_after_seconds))),
        )
        if result is None:
            raise _error("not_found", "Host was not found.", status_code=404)
        return result

    @app.get("/processes", tags=["processes"])
    @app.get("/api/processes", tags=["processes"], include_in_schema=False)
    async def processes(
        search: str = Query(default="", max_length=255),
        host: str | None = Query(default=None, max_length=255),
        cpu_min: float | None = Query(default=None, ge=0, le=1_000),
        memory_min: float | None = Query(default=None, ge=0, le=100),
        alert_only: bool = False,
        page: int = Query(default=1, ge=1, le=100_000),
        page_size: int = Query(default=50, ge=1, le=250),
        sort: str = Query(default="cpu_percent", max_length=32),
        order: str = Query(default="desc", pattern="^(asc|desc)$"),
    ) -> dict[str, Any]:
        items, total = db.list_processes(
            search=search,
            host=host,
            cpu_min=cpu_min,
            memory_min=memory_min,
            alert_only=alert_only,
            page=page,
            page_size=page_size,
            sort=sort,
            order=order,
            lookback_seconds=max(settings.default_window_seconds, int(settings.offline_after_seconds)),
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.get("/processes/{host}/{pid}", tags=["processes"])
    @app.get("/api/processes/{host}/{pid}", tags=["processes"], include_in_schema=False)
    async def process_detail(
        host: str,
        pid: int,
        create_time: float | None = Query(default=None, gt=0),
        window: int = Query(default=86_400, ge=60, le=2_592_000),
    ) -> dict[str, Any]:
        result = db.get_process_instance(host, pid, create_time, lookback_seconds=window)
        if result is None:
            raise _error("not_found", "Process instance was not found.", status_code=404)
        return result

    @app.get("/history", tags=["history"])
    @app.get("/api/history", tags=["history"], include_in_schema=False)
    async def history(
        host: str | None = Query(default=None, max_length=255),
        pid: int | None = Query(default=None, ge=0),
        create_time: float | None = Query(default=None, gt=0),
        start: float | None = Query(default=None, gt=0),
        end: float | None = Query(default=None, gt=0),
        search: str = Query(default="", max_length=255),
        limit: int = Query(default=100, ge=1, le=5_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = db.history(
            host=host, pid=pid, create_time=create_time, start=start, end=end,
            search=search, limit=limit, offset=offset,
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/alerts", tags=["alerts"])
    @app.get("/api/alerts", tags=["alerts"], include_in_schema=False)
    async def alerts(
        status: str = Query(default="all", pattern="^(all|active|resolved)$"),
        search: str = Query(default="", max_length=255),
        severity: str | None = Query(default=None, pattern="^(info|warning|critical)$"),
        host: str | None = Query(default=None, max_length=255),
        rule_id: str | None = Query(default=None, max_length=80),
        since: float | None = Query(default=None, gt=0),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = db.list_alerts(
            status=status, search=search, severity=severity, host=host,
            rule_id=rule_id, since=since, limit=limit, offset=offset,
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/alerts/summary", tags=["alerts"])
    @app.get("/api/alerts/summary", tags=["alerts"], include_in_schema=False)
    async def alerts_summary() -> dict[str, Any]:
        with db.connect() as connection:
            rows = connection.execute("SELECT severity, status, COUNT(*) AS count FROM alerts GROUP BY severity, status").fetchall()
        by_severity = {"info": 0, "warning": 0, "critical": 0}
        by_status = {"active": 0, "resolved": 0}
        for row in rows:
            by_severity[row["severity"]] = by_severity.get(row["severity"], 0) + int(row["count"])
            by_status[row["status"]] = by_status.get(row["status"], 0) + int(row["count"])
        return {"active": db.active_alert_count(), "by_severity": by_severity, "by_status": by_status}

    @app.get("/alerts/rules", tags=["rules"], response_model=RuleListResponse)
    @app.get("/api/alerts/rules", tags=["rules"], include_in_schema=False, response_model=RuleListResponse)
    async def list_rules() -> dict[str, Any]:
        items = db.list_rules()
        for item in items:
            item["enabled"] = bool(item["enabled"])
        return {"items": items, "total": len(items)}

    @app.get("/alerts/rules/{rule_id}", tags=["rules"], response_model=RuleResponse)
    @app.get("/api/alerts/rules/{rule_id}", tags=["rules"], include_in_schema=False, response_model=RuleResponse)
    async def get_rule(rule_id: str) -> dict[str, Any]:
        rule = db.get_rule(rule_id)
        if rule is None:
            raise _error("not_found", "Rule was not found.", status_code=404)
        rule["enabled"] = bool(rule["enabled"])
        return rule

    @app.post("/alerts/rules", tags=["rules"], status_code=201, response_model=RuleResponse)
    @app.post("/api/alerts/rules", tags=["rules"], status_code=201, include_in_schema=False, response_model=RuleResponse)
    async def create_rule(rule: RuleCreate) -> dict[str, Any]:
        try:
            created = db.create_rule(rule.model_dump())
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise _error("already_exists", "A rule with that ID already exists.", status_code=409) from exc
            logger.exception("Rule creation failed")
            raise _error("rule_create_failed", "The rule could not be created.", status_code=500) from exc
        created["enabled"] = bool(created["enabled"])
        return created

    @app.put("/alerts/rules/{rule_id}", tags=["rules"], response_model=RuleResponse)
    @app.patch("/alerts/rules/{rule_id}", tags=["rules"], include_in_schema=False, response_model=RuleResponse)
    @app.put("/api/alerts/rules/{rule_id}", tags=["rules"], include_in_schema=False, response_model=RuleResponse)
    @app.patch("/api/alerts/rules/{rule_id}", tags=["rules"], include_in_schema=False, response_model=RuleResponse)
    async def update_rule(rule_id: str, rule: RuleUpdate) -> dict[str, Any]:
        updated = db.update_rule(rule_id, rule.model_dump())
        if updated is None:
            raise _error("not_found", "Rule was not found.", status_code=404)
        updated["enabled"] = bool(updated["enabled"])
        return updated

    @app.delete("/alerts/rules/{rule_id}", tags=["rules"])
    @app.delete("/api/alerts/rules/{rule_id}", tags=["rules"], include_in_schema=False)
    async def delete_rule(rule_id: str) -> dict[str, Any]:
        if not db.delete_rule(rule_id):
            raise _error("not_found", "Rule was not found.", status_code=404)
        return {"status": "deleted", "rule_id": rule_id}

    # Serve the self-contained dashboard when the collector is run directly.
    # Docker uses the dedicated nginx UI service, but this makes local startup
    # and smoke testing a single command experience.
    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    if ui_dir.exists() and (ui_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app


app = create_app()
