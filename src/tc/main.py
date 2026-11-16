"""The loop: one process, fixed-rate ticks on a monotonic clock, clean stop on SIGTERM."""

from __future__ import annotations

import signal
import threading
import time

import pyarrow as pa

from tc.collector import Collector
from tc.common.clock import Ticker
from tc.common.config import Settings, get_settings
from tc.common.log import get_logger, setup_logging
from tc.ingest.fetch import Fetcher

log = get_logger(__name__)


def run(
    settings: Settings,
    collector: Collector,
    stop: threading.Event,
    monotonic=time.monotonic,
) -> None:
    ticker = Ticker(settings.poll_seconds, monotonic)
    deadline = monotonic() + settings.run_for_minutes * 60 if settings.run_for_minutes else None
    log.info(
        "collector started",
        extra={
            "poll_seconds": settings.poll_seconds,
            "run_for_minutes": settings.run_for_minutes,
            "dry_run": settings.dry_run,
            "data_dir": str(settings.data_dir),
        },
    )
    while not stop.is_set():
        try:
            collector.poll_once()
        except Exception:  # the loop must never die: log and keep polling
            log.exception("poll cycle failed")
        skipped_before = ticker.skipped
        wait = ticker.next_wait()
        collector.note_overrun(ticker.skipped - skipped_before)
        if deadline is not None and monotonic() + wait >= deadline:
            stop.wait(max(0.0, deadline - monotonic()))
            break
        stop.wait(wait)  # returns early on SIGTERM


def main() -> int:
    settings = get_settings()
    setup_logging()
    # The container gets 0.5 CPU: extra Arrow threads add no speed, only per-thread memory
    # (measured: compaction peak 391 MB -> 335 MB on a busy synthetic day, same duration).
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    stop = threading.Event()

    def on_signal(signum, _frame) -> None:
        log.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    fetcher = Fetcher(settings)
    collector = Collector(settings, fetcher)
    try:
        run(settings, collector, stop)
    finally:
        collector.shutdown()  # flush state even on an unexpected error
        fetcher.close()
    return 0
