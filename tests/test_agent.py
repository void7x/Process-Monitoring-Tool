from __future__ import annotations

import json
from pathlib import Path

from agent.buffer import LocalBuffer
from agent.sampler import ProcessSampler


def test_local_buffer_is_bounded_and_round_trips(tmp_path: Path):
    buffer = LocalBuffer(tmp_path / "buffer.jsonl", max_batches=2)
    for index in range(4):
        buffer.append({"host": "test", "samples": [{"pid": index}]})
    records = buffer.read()
    assert len(records) == 2
    assert records[0]["samples"][0]["pid"] == 2
    buffer.replace([])
    assert buffer.read() == []


def test_sampler_returns_safe_process_shape():
    samples = ProcessSampler("local-test").sample()
    assert isinstance(samples, list)
    if samples:
        required = {"pid", "process_name", "create_time", "timestamp", "cpu_percent", "memory_percent", "memory_rss", "read_bytes", "write_bytes"}
        assert required.issubset(samples[0])
        assert all(item["create_time"] > 0 for item in samples)
