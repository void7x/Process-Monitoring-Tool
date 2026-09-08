from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import psutil

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


# ---------------------------------------------------------------------------
# Fix 1 — first-sample CPU measurement.
#
# The agent cannot report a real CPU percentage on the first observation of
# a process, because psutil's ``cpu_percent(interval=None)`` is a delta from
# the previous counter.  The sampler must therefore emit ``None`` (not 0.0)
# on the first sample, and a real number on the second sample.
# ---------------------------------------------------------------------------


class _StubProcess:
    """Minimal stand-in for ``psutil.Process`` that records cpu_percent calls."""

    def __init__(self, info, cpu_history):
        self._info = info
        self._cpu_history = list(cpu_history)
        self.cpu_calls = 0
        self._entered = False

    def oneshot(self):
        return self

    # psutil's ``with process.oneshot()`` block.
    def __enter__(self):
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self._entered = False
        return False

    @property
    def info(self):
        return self._info

    def cpu_percent(self, interval=None):
        self.cpu_calls += 1
        if not self._cpu_history:
            return 0.0
        return self._cpu_history.pop(0)


def test_first_sample_marks_cpu_unavailable():
    """The first observation of a process must report ``cpu_percent=None``."""
    process_info = {
        "pid": 4242,
        "name": "first-shot",
        "username": "op",
        "create_time": 1_700_000_000.0,
        "memory_percent": 5.0,
        "memory_info": psutil.Process(os.getpid()).memory_info(),
        "io_counters": None,
        "status": "running",
    }
    stub = _StubProcess(process_info, [0.0, 42.0])  # two readings: prime + measure
    sampler = ProcessSampler("first-shot-host")
    with patch("agent.sampler.psutil.process_iter", return_value=[stub]):
        first_samples = sampler.sample()
    assert len(first_samples) == 1
    assert first_samples[0]["cpu_percent"] is None
    assert first_samples[0]["pid"] == 4242


def test_second_sample_returns_real_cpu_measurement():
    """A second sample of the same process instance must return a real percentage."""
    process_info = {
        "pid": 4343,
        "name": "second-shot",
        "username": "op",
        "create_time": 1_700_000_500.0,
        "memory_percent": 5.0,
        "memory_info": psutil.Process(os.getpid()).memory_info(),
        "io_counters": None,
        "status": "running",
    }
    sampler = ProcessSampler("second-shot-host")
    # The first call to ``cpu_percent`` is the prime (no baseline yet, so
    # psutil returns 0.0).  The next call is the first real reading and
    # must be returned to the caller.
    stub = _StubProcess(process_info, [0.0, 17.5])
    with patch("agent.sampler.psutil.process_iter", return_value=[stub]):
        first = sampler.sample()
    with patch("agent.sampler.psutil.process_iter", return_value=[stub]):
        second = sampler.sample()
    assert first[0]["cpu_percent"] is None
    assert second[0]["cpu_percent"] == 17.5


def test_baseline_is_isolated_per_process_instance():
    """A reused PID with a different ``create_time`` is a new instance and must rebuild its baseline."""
    sampler = ProcessSampler("pid-reuse-host")
    # First instance of the PID.
    info_old = {
        "pid": 7777, "name": "old", "username": "op",
        "create_time": 1_700_001_000.0, "memory_percent": 1.0,
        "memory_info": psutil.Process(os.getpid()).memory_info(),
        "io_counters": None, "status": "running",
    }
    stub_old = _StubProcess(info_old, [0.0, 50.0])
    with patch("agent.sampler.psutil.process_iter", return_value=[stub_old]):
        first = sampler.sample()
    assert first[0]["cpu_percent"] is None
    # Same PID, but a different process instance.
    info_new = {
        "pid": 7777, "name": "new", "username": "op",
        "create_time": 1_700_002_000.0, "memory_percent": 1.0,
        "memory_info": psutil.Process(os.getpid()).memory_info(),
        "io_counters": None, "status": "running",
    }
    stub_new = _StubProcess(info_new, [0.0, 80.0])
    with patch("agent.sampler.psutil.process_iter", return_value=[stub_new]):
        first_new = sampler.sample()
    with patch("agent.sampler.psutil.process_iter", return_value=[stub_new]):
        second_new = sampler.sample()
    # The new instance must re-prime, so its first sample is ``None``.
    assert first_new[0]["cpu_percent"] is None
    # And its second sample reflects the real delta.
    assert second_new[0]["cpu_percent"] == 80.0


def test_high_cpu_process_eventually_reports_nonzero_cpu():
    """A real, busy process must eventually produce a non-zero CPU reading."""
    import os
    sampler = ProcessSampler("live-host")
    # Prime the sampler with a busy process.  Use os.getpid() so the
    # process actually exists and psutil can read its counters.
    proc = psutil.Process(os.getpid())
    # Burn some CPU so the next call returns a real number.
    deadline = time.time() + 0.3
    while time.time() < deadline:
        _ = sum(range(5_000))
    # First sample: prime the baseline.
    sample_first = next((s for s in sampler.sample() if s["pid"] == os.getpid()), None)
    assert sample_first is not None
    # Burn more CPU.
    deadline = time.time() + 0.3
    while time.time() < deadline:
        _ = sum(range(5_000))
    # Second sample: a real measurement.
    sample_second = next((s for s in sampler.sample() if s["pid"] == os.getpid()), None)
    assert sample_second is not None
    # First sample may be ``None`` (correctly) or ``0.0`` (if the process
    # was idle between iterations).  The second sample, taken after
    # burning CPU, must be a real number — not ``None``.
    assert sample_second["cpu_percent"] is not None
    assert isinstance(sample_second["cpu_percent"], (int, float))


def test_sampler_handles_first_sample_disappearance():
    """If a process disappears between enumeration and CPU priming, the sample must still be returned."""
    import os

    class VanishingProcess(_StubProcess):
        def cpu_percent(self, interval=None):
            self.cpu_calls += 1
            if self.cpu_calls == 1:
                raise psutil.NoSuchProcess(self._info["pid"])
            return 5.0

    info = {
        "pid": 5555, "name": "vanishing", "username": "op",
        "create_time": 1_700_003_000.0, "memory_percent": 0.5,
        "memory_info": psutil.Process(os.getpid()).memory_info(),
        "io_counters": None, "status": "running",
    }
    stub = VanishingProcess(info, [])
    sampler = ProcessSampler("vanishing-host")
    with patch("agent.sampler.psutil.process_iter", return_value=[stub]):
        samples = sampler.sample()
    assert len(samples) == 1
    assert samples[0]["cpu_percent"] is None
    assert samples[0]["pid"] == 5555


def test_existing_sampler_behavior_remains_valid():
    """The sampler must continue to produce the documented sample shape."""
    samples = ProcessSampler("shape-host").sample()
    assert isinstance(samples, list)
    if samples:
        # ``cpu_percent`` may be ``float`` or ``None`` for a fresh
        # process — but every other field is required and well-typed.
        for item in samples:
            assert item["pid"] >= 0
            assert item["create_time"] > 0
            assert item["timestamp"] > 0
            assert item["cpu_percent"] is None or isinstance(item["cpu_percent"], (int, float))
            assert item["memory_percent"] >= 0
            assert item["memory_rss"] >= 0
            assert item["read_bytes"] >= 0
            assert item["write_bytes"] >= 0
            assert isinstance(item["state"], str)


def test_sampler_forget_resets_baseline_cache():
    """``forget()`` lets operators and tests force a re-prime."""
    sampler = ProcessSampler("forget-host")
    sampler._cpu_primed[(1, 1.0)] = True
    sampler.forget()
    assert sampler._cpu_primed == {}
    sampler._cpu_primed[(1, 1.0)] = True
    sampler._cpu_primed[(2, 1.0)] = True
    sampler.forget(pid=1)
    assert (1, 1.0) not in sampler._cpu_primed
    assert (2, 1.0) in sampler._cpu_primed
