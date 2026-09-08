"""Command-line entry point for the Windows/Linux monitoring agent."""
from __future__ import annotations

import argparse
import logging
import signal
import threading
from typing import Any

from .buffer import LocalBuffer
from .client import CollectorClient
from .config import AgentSettings
from .sampler import ProcessSampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("process_monitor.agent")


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def run(settings: AgentSettings | None = None, stop_event: threading.Event | None = None, *, max_cycles: int | None = None) -> None:
    settings = settings or AgentSettings.from_env()
    stop_event = stop_event or threading.Event()
    sampler = ProcessSampler(settings.hostname)
    client = CollectorClient(settings.collector_url, settings.request_timeout_seconds, settings.retry_attempts)
    buffer = LocalBuffer(settings.buffer_file, settings.max_buffer_batches)
    cycle = 0
    logger.info(
        "Agent started",
        extra={"host": settings.hostname, "collector": settings.collector_url, "interval": settings.sample_interval_seconds},
    )
    while not stop_event.is_set() and (max_cycles is None or cycle < max_cycles):
        cycle += 1
        try:
            # Drain oldest batches first so an outage does not starve history.
            pending = buffer.read()
            if pending:
                remaining = pending[:]
                while remaining and not stop_event.is_set():
                    if client.send(remaining[0]):
                        remaining.pop(0)
                    else:
                        break
                buffer.replace(remaining)

            samples = sampler.sample()
            for batch in _chunks(samples, settings.batch_size):
                payload = {"host": settings.hostname, "samples": batch}
                if not client.send(payload):
                    buffer.append(payload)
            logger.info("Sample cycle complete", extra={"host": settings.hostname, "processes": len(samples), "buffered_batches": buffer.pending_count()})
            if max_cycles is not None and cycle >= max_cycles:
                break
        except Exception:
            # A bad process or transient filesystem issue must not terminate a
            # long-running Windows service. The next cycle gets another chance.
            logger.exception("Sample cycle failed")
        stop_event.wait(settings.sample_interval_seconds)
    logger.info("Agent stopped", extra={"host": settings.hostname, "cycles": cycle})


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect local process metrics and send them to a Process Monitor collector")
    parser.add_argument("--once", action="store_true", help="Collect one sample cycle and exit")
    args = parser.parse_args()
    stop_event = threading.Event()

    def stop_handler(signum: int, _: Any) -> None:
        logger.info("Shutdown requested", extra={"signal": signum})
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), stop_handler)
    run(stop_event=stop_event, max_cycles=1 if args.once else None)


if __name__ == "__main__":
    main()
