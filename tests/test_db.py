from __future__ import annotations

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
