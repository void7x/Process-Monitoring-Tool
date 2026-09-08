"""SQLite repository for samples, hosts, rules, alert state, and alerts.

All SQL lives in this module. Routes and alert evaluation use this repository
instead of reaching into SQLite directly, which keeps the API surface small and
makes the process-instance invariants easy to test.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
    host TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    pid INTEGER NOT NULL CHECK(pid >= 0),
    process_name TEXT NOT NULL,
    username TEXT,
    create_time REAL NOT NULL,
    timestamp REAL NOT NULL,
    received_at REAL NOT NULL,
    cpu_percent REAL NOT NULL,
    memory_percent REAL NOT NULL,
    memory_rss INTEGER NOT NULL,
    read_bytes INTEGER NOT NULL,
    write_bytes INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',
    UNIQUE(host, pid, create_time, timestamp)
);

CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold REAL NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    consecutive_samples INTEGER NOT NULL DEFAULT 1,
    action TEXT NOT NULL DEFAULT 'none',
    cooldown REAL NOT NULL DEFAULT 300,
    severity TEXT NOT NULL DEFAULT 'warning',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_states (
    rule_id TEXT NOT NULL,
    host TEXT NOT NULL,
    pid INTEGER NOT NULL,
    create_time REAL NOT NULL,
    true_since REAL,
    consecutive_samples INTEGER NOT NULL DEFAULT 0,
    last_sample_at REAL,
    cooldown_until REAL NOT NULL DEFAULT 0,
    active_alert_id INTEGER,
    PRIMARY KEY(rule_id, host, pid, create_time)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    host TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_name TEXT NOT NULL,
    username TEXT,
    create_time REAL NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold REAL NOT NULL,
    current_value REAL NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    triggered_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    resolved_at REAL,
    action TEXT NOT NULL DEFAULT 'none'
);

CREATE INDEX IF NOT EXISTS idx_samples_host_received ON samples(host, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_samples_instance_time ON samples(host, pid, create_time, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON samples(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_samples_process_name ON samples(process_name);
CREATE INDEX IF NOT EXISTS idx_alerts_status_triggered ON alerts(status, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_instance ON alerts(host, pid, create_time, status);
CREATE INDEX IF NOT EXISTS idx_alerts_rule ON alerts(rule_id, status);
CREATE INDEX IF NOT EXISTS idx_states_instance ON alert_states(host, pid, create_time);
"""


class Database:
    """Small synchronous SQLite repository.

    SQLite connections are short lived and WAL mode is enabled. This is a good
    fit for the collector's modest write volume and keeps the code compatible
    with Windows, Linux, and the standard library.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            # Upgrade the one column used by newer freshness queries before
            # creating indexes. This ordering matters for an existing MVP DB:
            # SQLite would otherwise fail while creating an index on a column
            # that has not been added yet.
            existing = {row[1] for row in connection.execute("PRAGMA table_info(samples)")}
            if existing:
                # Early MVP databases used a smaller sample row. Additive
                # migrations keep their data usable rather than replacing the
                # file. Defaults are only used for fields that did not exist in
                # that older schema.
                additions = {
                    "username": "TEXT",
                    "memory_rss": "INTEGER NOT NULL DEFAULT 0",
                    "state": "TEXT NOT NULL DEFAULT 'running'",
                    "received_at": "REAL",
                }
                for column, definition in additions.items():
                    if column not in existing:
                        connection.execute(f"ALTER TABLE samples ADD COLUMN {column} {definition}")
                if "user" in existing:
                    connection.execute("UPDATE samples SET username = user WHERE username IS NULL")
                connection.execute(
                    "UPDATE samples SET received_at = COALESCE(received_at, timestamp, ?) WHERE received_at IS NULL",
                    (time.time(),),
                )
            connection.executescript(SCHEMA)
            # Some early databases relied on application-level duplicate
            # checks. De-duplicate once before adding the durable unique index.
            connection.execute(
                """
                DELETE FROM samples WHERE id NOT IN (
                    SELECT MIN(id) FROM samples GROUP BY host, pid, create_time, timestamp
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_samples_identity ON samples(host, pid, create_time, timestamp)"
            )
            connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def _with_compat_fields(value: dict[str, Any]) -> dict[str, Any]:
        # The original MVP called this field `user`; the normalized storage and
        # new UI use `username`. Returning both keeps existing integrations
        # working without duplicating a column or losing validation.
        if "username" in value and "user" not in value:
            value["user"] = value["username"]
        return value

    @classmethod
    def _dict(cls, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return cls._with_compat_fields(dict(row)) if row is not None else None

    @classmethod
    def _dicts(cls, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [cls._with_compat_fields(dict(row)) for row in rows]

    def insert_samples(self, host: str, samples: Iterable[dict[str, Any]], received_at: float | None = None) -> dict[str, Any]:
        """Insert a batch idempotently using the exact sample identity.

        The unique key is host + pid + create_time + timestamp. A process that
        reuses a PID therefore gets an independent history, while retried HTTP
        payloads do not create duplicate rows.
        """
        received_at = received_at or time.time()
        accepted = 0
        duplicates = 0
        inserted: list[dict[str, Any]] = []
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for sample in samples:
                values = (
                    host,
                    int(sample["pid"]),
                    str(sample["process_name"]),
                    sample.get("username"),
                    float(sample["create_time"]),
                    float(sample["timestamp"]),
                    received_at,
                    float(sample["cpu_percent"]),
                    float(sample["memory_percent"]),
                    int(sample["memory_rss"]),
                    int(sample["read_bytes"]),
                    int(sample["write_bytes"]),
                    str(sample.get("state") or "running"),
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO samples
                    (host, pid, process_name, username, create_time, timestamp,
                     received_at, cpu_percent, memory_percent, memory_rss,
                     read_bytes, write_bytes, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                if cursor.rowcount == 1:
                    accepted += 1
                    connection.execute(
                        """
                        INSERT INTO hosts(host, first_seen, last_seen, sample_count)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(host) DO UPDATE SET
                            first_seen = MIN(first_seen, excluded.first_seen),
                            last_seen = MAX(last_seen, excluded.last_seen),
                            sample_count = sample_count + 1
                        """,
                        (host, received_at, received_at),
                    )
                    inserted.append(
                        {
                            "host": host,
                            "pid": values[1],
                            "process_name": values[2],
                            "username": values[3],
                            "create_time": values[4],
                            "timestamp": values[5],
                            "received_at": values[6],
                            "cpu_percent": values[7],
                            "memory_percent": values[8],
                            "memory_rss": values[9],
                            "read_bytes": values[10],
                            "write_bytes": values[11],
                            "state": values[12],
                        }
                    )
                else:
                    duplicates += 1
            connection.commit()
        return {"accepted": accepted, "duplicates": duplicates, "inserted": inserted}

    def prune_samples(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86_400
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            return cursor.rowcount

    def count_samples_since(self, since: float) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM samples WHERE received_at >= ?", (since,)).fetchone()
            return int(row["count"])

    # ---------- Rules ----------
    def list_rules(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM rules"
        params: list[Any] = []
        if enabled is not None:
            sql += " WHERE enabled = ?"
            params.append(1 if enabled else 0)
        sql += " ORDER BY enabled DESC, name COLLATE NOCASE"
        with self.connect() as connection:
            return self._dicts(connection.execute(sql, params).fetchall())

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._dict(connection.execute("SELECT * FROM rules WHERE rule_id = ?", (rule_id,)).fetchone())

    def create_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO rules(rule_id, name, metric, operator, threshold, duration,
                    consecutive_samples, action, cooldown, severity, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule["rule_id"], rule["name"], rule["metric"], rule["operator"], rule["threshold"],
                    rule["duration"], rule["consecutive_samples"], rule["action"], rule["cooldown"],
                    rule["severity"], int(rule["enabled"]), now, now,
                ),
            )
        return self.get_rule(rule["rule_id"])  # type: ignore[return-value]

    def update_rule(self, rule_id: str, rule: dict[str, Any]) -> dict[str, Any] | None:
        now = time.time()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE rules SET name=?, metric=?, operator=?, threshold=?, duration=?,
                    consecutive_samples=?, action=?, cooldown=?, severity=?, enabled=?, updated_at=?
                WHERE rule_id=?
                """,
                (
                    rule["name"], rule["metric"], rule["operator"], rule["threshold"], rule["duration"],
                    rule["consecutive_samples"], rule["action"], rule["cooldown"], rule["severity"],
                    int(rule["enabled"]), now, rule_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            connection.execute("DELETE FROM alert_states WHERE rule_id = ?", (rule_id,))
            return cursor.rowcount > 0

    # ---------- Alert state and alerts ----------
    def get_alert_state(self, rule_id: str, host: str, pid: int, create_time: float) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._dict(
                connection.execute(
                    """
                    SELECT * FROM alert_states
                    WHERE rule_id=? AND host=? AND pid=? AND create_time=?
                    """, (rule_id, host, pid, create_time)
                ).fetchone()
            )

    def save_alert_state(self, state: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_states(rule_id, host, pid, create_time, true_since,
                    consecutive_samples, last_sample_at, cooldown_until, active_alert_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id, host, pid, create_time) DO UPDATE SET
                    true_since=excluded.true_since,
                    consecutive_samples=excluded.consecutive_samples,
                    last_sample_at=excluded.last_sample_at,
                    cooldown_until=excluded.cooldown_until,
                    active_alert_id=excluded.active_alert_id
                """,
                (
                    state["rule_id"], state["host"], state["pid"], state["create_time"], state.get("true_since"),
                    state.get("consecutive_samples", 0), state.get("last_sample_at"), state.get("cooldown_until", 0),
                    state.get("active_alert_id"),
                ),
            )

    def get_active_alert(self, rule_id: str, host: str, pid: int, create_time: float) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._dict(
                connection.execute(
                    """
                    SELECT * FROM alerts
                    WHERE rule_id=? AND host=? AND pid=? AND create_time=? AND status='active'
                    ORDER BY id DESC LIMIT 1
                    """, (rule_id, host, pid, create_time)
                ).fetchone()
            )

    def create_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO alerts(rule_id, rule_name, host, pid, process_name, username,
                    create_time, metric, operator, threshold, current_value, severity,
                    status, triggered_at, last_seen, action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    alert["rule_id"], alert["rule_name"], alert["host"], alert["pid"], alert["process_name"],
                    alert.get("username"), alert["create_time"], alert["metric"], alert["operator"],
                    alert["threshold"], alert["current_value"], alert["severity"], alert["triggered_at"],
                    alert["last_seen"], alert.get("action", "none"),
                ),
            )
            alert_id = cursor.lastrowid
            return self._dict(connection.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone())  # type: ignore[return-value]

    def update_alert(self, alert_id: int, current_value: float, last_seen: float, process_name: str, username: str | None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE alerts SET current_value=?, last_seen=?, process_name=?, username=? WHERE id=? AND status='active'",
                (current_value, last_seen, process_name, username, alert_id),
            )

    def resolve_alert(self, alert_id: int, resolved_at: float, current_value: float | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE alerts SET status='resolved', resolved_at=?, last_seen=?,
                    current_value=COALESCE(?, current_value)
                WHERE id=? AND status='active'
                """, (resolved_at, resolved_at, current_value, alert_id)
            )

    def list_alerts(
        self,
        *,
        status: str = "all",
        search: str = "",
        severity: str | None = None,
        host: str | None = None,
        rule_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["1=1"]
        params: list[Any] = []
        if status in {"active", "resolved"}:
            clauses.append("a.status = ?")
            params.append(status)
        if severity in {"info", "warning", "critical"}:
            clauses.append("a.severity = ?")
            params.append(severity)
        if host:
            clauses.append("a.host = ?")
            params.append(host)
        if rule_id:
            clauses.append("a.rule_id = ?")
            params.append(rule_id)
        if since is not None:
            clauses.append("a.triggered_at >= ?")
            params.append(since)
        if search:
            like = f"%{search}%"
            clauses.append("(a.host LIKE ? OR a.process_name LIKE ? OR CAST(a.pid AS TEXT) LIKE ? OR a.rule_name LIKE ? OR a.metric LIKE ?)")
            params.extend([like, like, like, like, like])
        where = " AND ".join(clauses)
        sql = f"SELECT a.* FROM alerts a WHERE {where} ORDER BY a.triggered_at DESC, a.id DESC LIMIT ? OFFSET ?"
        count_sql = f"SELECT COUNT(*) AS count FROM alerts a WHERE {where}"
        with self.connect() as connection:
            total = int(connection.execute(count_sql, params).fetchone()["count"])
            rows = self._dicts(connection.execute(sql, [*params, limit, offset]).fetchall())
        return rows, total

    def active_alert_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) AS count FROM alerts WHERE status='active'").fetchone()["count"])

    # ---------- Snapshot and history ----------
    def _latest_cte(self, cutoff: float, host: str | None = None) -> tuple[str, list[Any]]:
        clauses = ["received_at >= ?"]
        params: list[Any] = [cutoff]
        if host:
            clauses.append("host = ?")
            params.append(host)
        c = " AND ".join(clauses)
        return (
            f"""
            WITH ranked AS (
                SELECT s.*, ROW_NUMBER() OVER (
                    PARTITION BY host, pid, create_time ORDER BY timestamp DESC, id DESC
                ) AS rn
                FROM samples s WHERE {c}
            ), latest AS (SELECT * FROM ranked WHERE rn=1)
            """, params,
        )

    def latest_samples(self, *, host: str | None = None, lookback_seconds: int = 86_400, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        cte, params = self._latest_cte(now - lookback_seconds, host)
        with self.connect() as connection:
            return self._dicts(connection.execute(cte + " SELECT * FROM latest ORDER BY cpu_percent DESC", params).fetchall())

    def list_processes(
        self,
        *,
        search: str = "",
        host: str | None = None,
        cpu_min: float | None = None,
        memory_min: float | None = None,
        alert_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        sort: str = "cpu_percent",
        order: str = "desc",
        lookback_seconds: int = 86_400,
        now: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        now = now or time.time()
        cte, params = self._latest_cte(now - lookback_seconds, host)
        clauses = ["1=1"]
        params2: list[Any] = []
        if search:
            like = f"%{search}%"
            clauses.append("(l.process_name LIKE ? OR l.host LIKE ? OR l.username LIKE ? OR CAST(l.pid AS TEXT) LIKE ?)")
            params2.extend([like, like, like, like])
        if cpu_min is not None:
            clauses.append("l.cpu_percent >= ?")
            params2.append(cpu_min)
        if memory_min is not None:
            clauses.append("l.memory_percent >= ?")
            params2.append(memory_min)
        if alert_only:
            clauses.append("EXISTS (SELECT 1 FROM alerts a WHERE a.host=l.host AND a.pid=l.pid AND a.create_time=l.create_time AND a.status='active')")
        where = " AND ".join(clauses)
        sort_map = {
            "process_name": "l.process_name COLLATE NOCASE",
            "pid": "l.pid",
            "host": "l.host COLLATE NOCASE",
            "cpu_percent": "l.cpu_percent",
            "memory_percent": "l.memory_percent",
            "memory_rss": "l.memory_rss",
            "read_bytes": "l.read_bytes",
            "write_bytes": "l.write_bytes",
            "received_at": "l.received_at",
        }
        order_sql = "DESC" if order.lower() == "desc" else "ASC"
        sort_sql = sort_map.get(sort, "l.cpu_percent")
        base = cte + f"""
            SELECT l.*,
                CASE WHEN EXISTS (
                    SELECT 1 FROM alerts a WHERE a.host=l.host AND a.pid=l.pid
                        AND a.create_time=l.create_time AND a.status='active'
                ) THEN 1 ELSE 0 END AS has_active_alert
            FROM latest l WHERE {where}
        """
        count_sql = "SELECT COUNT(*) AS count FROM (" + base + ") counted"
        sql = base + f" ORDER BY {sort_sql} {order_sql}, l.id DESC LIMIT ? OFFSET ?"
        offset = (page - 1) * page_size
        with self.connect() as connection:
            total = int(connection.execute(count_sql, [*params, *params2]).fetchone()["count"])
            rows = self._dicts(connection.execute(sql, [*params, *params2, page_size, offset]).fetchall())
        return rows, total

    def list_hosts(self, *, search: str = "", lookback_seconds: int = 86_400, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        with self.connect() as connection:
            clauses = []
            params: list[Any] = []
            if search:
                clauses.append("host LIKE ?")
                params.append(f"%{search}%")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            hosts = self._dicts(connection.execute("SELECT * FROM hosts" + where + " ORDER BY host COLLATE NOCASE", params).fetchall())
        latest = self.latest_samples(lookback_seconds=lookback_seconds, now=now)
        by_host: dict[str, list[dict[str, Any]]] = {}
        for row in latest:
            by_host.setdefault(row["host"], []).append(row)
        results = []
        for host_row in hosts:
            rows = by_host.get(host_row["host"], [])
            results.append(self._host_summary(host_row, rows, now))
        return results

    @staticmethod
    def _host_summary(host_row: dict[str, Any], rows: list[dict[str, Any]], now: float, stale_after: float = 90, offline_after: float = 900) -> dict[str, Any]:
        last_seen = float(host_row["last_seen"])
        age = max(0.0, now - last_seen)
        status = "live" if age <= stale_after else "stale" if age <= offline_after else "offline"
        cpu = sum(max(0.0, float(row["cpu_percent"])) for row in rows)
        memory = min(100.0, sum(max(0.0, float(row["memory_percent"])) for row in rows))
        return {
            "host": host_row["host"],
            "first_seen": host_row["first_seen"],
            "last_seen": last_seen,
            "last_seen_age_seconds": age,
            "status": status,
            "process_count": len(rows) if status != "offline" else 0,
            "cpu_percent": cpu if status != "offline" else 0,
            "memory_percent": memory if status != "offline" else 0,
            "sample_count": host_row["sample_count"],
            "active_alert_count": 0,
        }

    def hosts_with_alert_counts(self, *, stale_after: float, offline_after: float, lookback_seconds: int, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        hosts = self.list_hosts(lookback_seconds=lookback_seconds, now=now)
        bucket = max(60, int(lookback_seconds / 12))
        start = now - lookback_seconds
        with self.connect() as connection:
            rows = connection.execute("SELECT host, COUNT(*) AS count FROM alerts WHERE status='active' GROUP BY host").fetchall()
            activity_rows = connection.execute(
                """
                SELECT host, CAST(received_at / ? AS INTEGER) * ? AS bucket, COUNT(*) AS count
                FROM samples WHERE received_at >= ? GROUP BY host, bucket ORDER BY bucket ASC
                """, (bucket, bucket, start)
            ).fetchall()
        counts = {row["host"]: int(row["count"]) for row in rows}
        activity_by_host: dict[str, dict[float, int]] = {}
        for row in activity_rows:
            activity_by_host.setdefault(row["host"], {})[float(row["bucket"])] = int(row["count"])
        buckets = [float((int(start / bucket) + index) * bucket) for index in range(13)]
        for host in hosts:
            host["active_alert_count"] = counts.get(host["host"], 0)
            host["activity"] = [{"timestamp": item, "value": activity_by_host.get(host["host"], {}).get(item, 0)} for item in buckets]
            # Apply configured thresholds after the repository's presentation
            # helper has calculated age.
            age = host["last_seen_age_seconds"]
            host["status"] = "live" if age <= stale_after else "stale" if age <= offline_after else "offline"
            if host["status"] == "offline":
                host["process_count"] = 0
                host["cpu_percent"] = 0
                host["memory_percent"] = 0
        return hosts

    def get_host(self, host: str, *, stale_after: float, offline_after: float, lookback_seconds: int, now: float | None = None) -> dict[str, Any] | None:
        now = now or time.time()
        with self.connect() as connection:
            host_row = self._dict(connection.execute("SELECT * FROM hosts WHERE host=?", (host,)).fetchone())
        if not host_row:
            return None
        rows = self.latest_samples(host=host, lookback_seconds=lookback_seconds, now=now)
        result = self._host_summary(host_row, rows, now, stale_after, offline_after)
        with self.connect() as connection:
            alert_rows = self._dicts(connection.execute("SELECT * FROM alerts WHERE host=? ORDER BY triggered_at DESC LIMIT 8", (host,)).fetchall())
        result["top_cpu"] = sorted(rows, key=lambda row: row["cpu_percent"], reverse=True)[:8]
        result["top_memory"] = sorted(rows, key=lambda row: row["memory_percent"], reverse=True)[:8]
        result["recent_alerts"] = alert_rows
        result["series"] = self.metrics_summary(host=host, lookback_seconds=lookback_seconds, now=now)
        return result

    def get_process_instance(
        self,
        host: str,
        pid: int,
        create_time: float | None,
        *,
        lookback_seconds: int = 2_592_000,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or time.time()
        with self.connect() as connection:
            if create_time is None:
                row = connection.execute(
                    """
                    SELECT create_time FROM samples WHERE host=? AND pid=?
                    ORDER BY timestamp DESC, id DESC LIMIT 1
                    """, (host, pid)
                ).fetchone()
                if row is None:
                    return None
                create_time = float(row["create_time"])
            current = self._dict(
                connection.execute(
                    """
                    SELECT * FROM samples WHERE host=? AND pid=? AND create_time=?
                    ORDER BY timestamp DESC, id DESC LIMIT 1
                    """, (host, pid, create_time)
                ).fetchone()
            )
            if current is None:
                return None
            history = self._dicts(
                connection.execute(
                    """
                    SELECT * FROM samples WHERE host=? AND pid=? AND create_time=? AND timestamp>=?
                    ORDER BY timestamp ASC, id ASC LIMIT 5000
                    """, (host, pid, create_time, now - lookback_seconds)
                ).fetchall()
            )
            alerts = self._dicts(
                connection.execute(
                    """
                    SELECT * FROM alerts WHERE host=? AND pid=? AND create_time=?
                    ORDER BY triggered_at DESC LIMIT 100
                    """, (host, pid, create_time)
                ).fetchall()
            )
        return {
            "instance_id": f"{host}:{pid}:{float(create_time):.6f}",
            "identity": {
                "host": host,
                "pid": pid,
                "process_name": current["process_name"],
                "username": current["username"],
                "create_time": create_time,
            },
            "current": current,
            "history": history,
            "alerts": alerts,
            "series": {
                "cpu": [{"timestamp": r["timestamp"], "value": r["cpu_percent"]} for r in history],
                "memory": [{"timestamp": r["timestamp"], "value": r["memory_percent"]} for r in history],
                "read_io": [{"timestamp": r["timestamp"], "value": r["read_bytes"]} for r in history],
                "write_io": [{"timestamp": r["timestamp"], "value": r["write_bytes"]} for r in history],
            },
        }

    def history(
        self,
        *,
        host: str | None = None,
        pid: int | None = None,
        create_time: float | None = None,
        start: float | None = None,
        end: float | None = None,
        search: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["1=1"]
        params: list[Any] = []
        if host:
            clauses.append("host=?")
            params.append(host)
        if pid is not None:
            clauses.append("pid=?")
            params.append(pid)
        if create_time is not None:
            clauses.append("create_time=?")
            params.append(create_time)
        if start is not None:
            clauses.append("timestamp>=?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp<=?")
            params.append(end)
        if search:
            like = f"%{search}%"
            clauses.append("(host LIKE ? OR process_name LIKE ? OR username LIKE ? OR CAST(pid AS TEXT) LIKE ?)")
            params.extend([like, like, like, like])
        where = " AND ".join(clauses)
        with self.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) AS count FROM samples WHERE {where}", params).fetchone()["count"])
            rows = self._dicts(
                connection.execute(
                    f"SELECT * FROM samples WHERE {where} ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
            )
        return rows, total

    # ---------- Aggregates ----------
    def metrics_summary(self, *, host: str | None = None, lookback_seconds: int = 3_600, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        start = now - lookback_seconds
        # A minute is readable for a day and still useful for the default hour.
        bucket = max(10, int(lookback_seconds / 60))
        clauses = ["timestamp >= ?"]
        params: list[Any] = [start]
        if host:
            clauses.append("host = ?")
            params.append(host)
        where = " AND ".join(clauses)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT CAST(timestamp / ? AS INTEGER) * ? AS bucket,
                    AVG(cpu_percent) AS avg_cpu,
                    AVG(memory_percent) AS avg_memory,
                    COUNT(*) AS activity
                FROM samples WHERE {where}
                GROUP BY bucket ORDER BY bucket ASC
                """, [bucket, bucket, *params]
            ).fetchall()
            alert_rows = connection.execute(
                """
                SELECT CAST(triggered_at / ? AS INTEGER) * ? AS bucket, COUNT(*) AS count
                FROM alerts WHERE triggered_at >= ? AND (? IS NULL OR host = ?)
                GROUP BY bucket ORDER BY bucket ASC
                """, (bucket, bucket, start, host, host)
            ).fetchall()
        return {
            "window_seconds": lookback_seconds,
            "bucket_seconds": bucket,
            "cpu": [{"timestamp": row["bucket"], "value": round(float(row["avg_cpu"] or 0), 3)} for row in rows],
            "memory": [{"timestamp": row["bucket"], "value": round(float(row["avg_memory"] or 0), 3)} for row in rows],
            "activity": [{"timestamp": row["bucket"], "value": int(row["activity"] or 0)} for row in rows],
            "alerts": [{"timestamp": row["bucket"], "value": int(row["count"] or 0)} for row in alert_rows],
        }

    def summary(self, *, stale_after: float, offline_after: float, lookback_seconds: int, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        hosts = self.hosts_with_alert_counts(
            stale_after=stale_after, offline_after=offline_after, lookback_seconds=max(offline_after, lookback_seconds), now=now
        )
        latest = self.latest_samples(lookback_seconds=max(offline_after, lookback_seconds), now=now)
        active_hosts = [host for host in hosts if host["status"] == "live"]
        stale_hosts = [host for host in hosts if host["status"] == "stale"]
        current_rows = [row for row in latest if now - float(row["received_at"]) <= offline_after]
        cpu = sum(max(0.0, float(row["cpu_percent"])) for row in current_rows)
        memory = min(100.0, sum(max(0.0, float(row["memory_percent"])) for row in current_rows))
        return {
            "generated_at": now,
            "window_seconds": lookback_seconds,
            "freshness": {
                "stale_after_seconds": stale_after,
                "offline_after_seconds": offline_after,
            },
            "kpis": {
                "live_hosts": len(active_hosts),
                "total_hosts": len(hosts),
                "total_running_processes": len(current_rows),
                "active_alerts": self.active_alert_count(),
                "cpu_percent": round(cpu, 2),
                "memory_percent": round(memory, 2),
                "samples_received": self.count_samples_since(now - lookback_seconds),
                "stale_hosts": len(stale_hosts),
                "offline_hosts": sum(1 for host in hosts if host["status"] == "offline"),
            },
            "hosts": hosts,
        }
