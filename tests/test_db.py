from __future__ import annotations

import time

from collector.db import Database
from .conftest import sample


def test_database_unique_identity(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.initialize()
    first = db.insert_samples("host", [sample(timestamp=1_700_000_010, create_time=1_700_000_000)])
    duplicate = db.insert_samples("host", [sample(timestamp=1_700_000_010, create_time=1_700_000_000)])
    reused = db.insert_samples("host", [sample(timestamp=1_700_000_010, create_time=1_700_000_100)])
    assert first["accepted"] == 1
    assert duplicate["duplicates"] == 1
    assert reused["accepted"] == 1
    rows, total = db.history(host="host")
    assert total == 2
    assert {row["create_time"] for row in rows} == {1_700_000_000, 1_700_000_100}


def _retention_db(tmp_path) -> Database:
    db = Database(tmp_path / "retention.sqlite")
    db.initialize()
    return db


def test_prune_samples_removes_old_keeps_recent(tmp_path):
    """Pruning must drop only samples whose ``timestamp`` is older than the cutoff."""
    db = _retention_db(tmp_path)
    now = time.time()
    # 40 days old, 10 days old, 1 day old
    old_a = sample(timestamp=now - 40 * 86_400, create_time=now - 41 * 86_400, pid=1)
    old_b = sample(timestamp=now - 35 * 86_400, create_time=now - 36 * 86_400, pid=2)
    fresh_a = sample(timestamp=now - 10 * 86_400, create_time=now - 11 * 86_400, pid=3)
    fresh_b = sample(timestamp=now - 1 * 86_400, create_time=now - 2 * 86_400, pid=4)
    assert db.insert_samples("host", [old_a, old_b, fresh_a, fresh_b])["accepted"] == 4
    # ``RETENTION_DAYS=30`` => samples older than 30 days are dropped.
    deleted = db.prune_samples(30)
    assert deleted == 2
    remaining, total = db.history(host="host")
    assert total == 2
    assert {row["pid"] for row in remaining} == {3, 4}


def test_prune_samples_is_idempotent(tmp_path):
    """Running the same pruning twice must be safe and the second run must delete nothing."""
    db = _retention_db(tmp_path)
    now = time.time()
    old = sample(timestamp=now - 60 * 86_400, create_time=now - 61 * 86_400, pid=10)
    fresh = sample(timestamp=now - 5 * 86_400, create_time=now - 6 * 86_400, pid=11)
    db.insert_samples("host", [old, fresh])
    first = db.prune_samples(30)
    second = db.prune_samples(30)
    assert first == 1
    assert second == 0
    _, total = db.history(host="host")
    assert total == 1


def test_prune_samples_on_empty_database(tmp_path):
    """Pruning an empty database must be safe and return 0."""
    db = _retention_db(tmp_path)
    assert db.prune_samples(30) == 0


def test_prune_samples_preserves_process_instance_identity(tmp_path):
    """Pruning must never collapse two different ``create_time`` rows that share a PID."""
    db = _retention_db(tmp_path)
    now = time.time()
    old_instance = sample(timestamp=now - 100 * 86_400, create_time=now - 101 * 86_400, pid=42)
    fresh_instance = sample(timestamp=now - 5 * 86_400, create_time=now - 6 * 86_400, pid=42)
    db.insert_samples("host", [old_instance, fresh_instance])
    db.prune_samples(30)
    rows, total = db.history(host="host", pid=42)
    assert total == 1
    assert rows[0]["create_time"] == fresh_instance["create_time"]


def test_prune_samples_keeps_history_query_internally_consistent(tmp_path):
    """After pruning, both ``history`` and ``latest_samples`` must agree."""
    db = _retention_db(tmp_path)
    now = time.time()
    for index, days_old in enumerate([100, 90, 80, 5, 1]):
        db.insert_samples(
            "host",
            [sample(timestamp=now - days_old * 86_400, create_time=now - (days_old + 1) * 86_400, pid=index)],
        )
    db.prune_samples(30)
    _, total = db.history(host="host")
    assert total == 2  # only the two recent ones survive
    latest = db.latest_samples(host="host", lookback_seconds=30 * 86_400, now=now)
    assert len(latest) == 2


def test_prune_samples_handles_unix_timestamp_cutoff(tmp_path):
    """The cutoff is a plain ``time.time()`` value; absolute timestamps must be honored."""
    db = _retention_db(tmp_path)
    # Use a fixed reference time so the test is deterministic.  retention=30
    # means samples whose timestamp is older than ``ref - 30*86400`` are
    # dropped.  The "old" sample is well before the cutoff, the "fresh"
    # sample is well after.
    ref = 1_700_000_000.0
    old = sample(timestamp=ref - 100 * 86_400, create_time=ref - 101 * 86_400, pid=1)
    fresh = sample(timestamp=ref - 5 * 86_400, create_time=ref - 6 * 86_400, pid=2)
    db.insert_samples("host", [old, fresh])
    import collector.db as db_module
    original = db_module.time.time
    db_module.time.time = lambda: ref
    try:
        deleted = db.prune_samples(30)
    finally:
        db_module.time.time = original
    assert deleted == 1
    rows, total = db.history(host="host")
    assert total == 1
    assert rows[0]["pid"] == 2
