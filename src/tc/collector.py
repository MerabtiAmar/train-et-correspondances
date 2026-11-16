"""One collection cycle and the housekeeping around it. No loop, no signals: see main.py.

Everything here is wrapped so that a bad snapshot, a full disk or a failed upload is logged and
counted but never stops the collector: the real-time feed cannot be replayed.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta

from tc.common.clock import now_utc, paris_date, paris_hour
from tc.common.config import Settings
from tc.common.log import get_logger
from tc.ingest.decode import FeedParseError, decode_feed, header_timestamp
from tc.ingest.fetch import Fetcher
from tc.ingest.parse_alerts import alert_rows
from tc.ingest.parse_tu import trip_update_rows
from tc.ingest.static_gtfs import load_state, load_static_index, refresh_static
from tc.store.coverage import (
    FeedStats,
    build_report,
    load_stats,
    save_stats,
    stats_path,
    write_report,
)
from tc.store.files import write_bytes_atomic
from tc.store.manifest import Manifest, pending_days
from tc.store.parquet import HourlyStore, cleanup_tmp, compact_day
from tc.store.raw import RawSampler
from tc.store.retention import enforce_retention
from tc.store.upload import Uploader

log = get_logger(__name__)

RETRY_AFTER = timedelta(hours=1)
IDLE_CHECK = timedelta(minutes=15)


class Collector:
    def __init__(
        self,
        settings: Settings,
        fetcher: Fetcher,
        uploader: Uploader | None = None,
        now: Callable[[], datetime] = now_utc,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.s = settings
        self.data_dir = settings.data_dir
        self.fetcher = fetcher
        self._now = now
        self._mono = monotonic

        removed = cleanup_tmp(self.data_dir)
        if removed:
            log.warning("removed leftover temp files from a crash", extra={"files": removed})
        self.manifest = Manifest(self.data_dir)
        self.uploader = uploader or Uploader(settings, self.manifest)
        self.tu_store = HourlyStore(self.data_dir, "reduced")
        self.alerts_store = HourlyStore(self.data_dir, "alerts")
        self.raw = RawSampler(self.data_dir, settings.raw_sample_every_min)
        self.stats = load_stats(self.data_dir, paris_date(self._now()))
        self.static_index = load_static_index(self.data_dir)

        self.raw_paused = False  # disk cap hit with nothing safe to delete
        self._last_ts = {"trip_updates": 0, "alerts": 0}
        self._last_source: str | None = None
        self._last_flush = self._mono()
        self._next_daily = self._now()
        self._next_static = self._now()
        self._done_dry_days: set[date] = set()
        self._heartbeat()

    # ---- one cycle -------------------------------------------------------------------------

    def poll_once(self) -> None:
        now = self._now()
        self._roll_day(now)
        self.stats.polls_done += 1
        self._poll_trip_updates(now)
        self._poll_alerts(now)
        self._heartbeat()
        self._guard("flush", self._maybe_flush)
        self._guard("daily upload", self._maybe_daily, now)
        self._guard("static GTFS", self._maybe_static, now)
        self._heartbeat()

    def note_overrun(self, skipped_ticks: int) -> None:
        if skipped_ticks:
            self.stats.polls_skipped_overrun += skipped_ticks
            log.warning("poll cycle overran, ticks skipped", extra={"skipped": skipped_ticks})

    def shutdown(self) -> None:
        """Flush everything to disk (called on SIGTERM / end of run)."""
        self._guard("final flush", self._flush_all)
        self._heartbeat()
        log.info("collector stopped", extra={"polls": self.stats.polls_done})

    @staticmethod
    def _guard(what: str, fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("step failed, continuing", extra={"step": what})

    # ---- feeds -----------------------------------------------------------------------------

    def _poll_trip_updates(self, now: datetime) -> None:
        st = self.stats.trip_updates
        urls = [u for u in (self.s.trip_updates_url, self.s.trip_updates_url_fallback) if u.strip()]
        chosen = stale = None
        saw_empty = False
        for url in urls:  # primary first; the fallback is only asked when it did not deliver
            res = self.fetcher.get(url)
            if res.status == "error":
                st.fetch_errors += 1
                log.warning("trip updates fetch failed", extra={"url": url, "error": res.error})
                continue
            if res.status == "not_modified":
                st.not_modified += 1
                return
            st.fetch_ok += 1
            try:
                msg = decode_feed(res.content)
            except FeedParseError as exc:
                st.parse_errors += 1
                log.error("trip updates not decodable", extra={"url": url, "error": str(exc)})
                continue
            ts = header_timestamp(msg)
            if not msg.entity:
                saw_empty = True
                continue
            if ts and now.timestamp() - ts > self.s.stale_after_min * 60:
                stale = stale or (url, res.content, msg, ts)
                continue
            chosen = (url, res.content, msg, ts)
            break
        if chosen is None and stale is not None:
            chosen = stale
            st.stale += 1
        elif chosen is None:
            if saw_empty:
                st.empty += 1
            log.warning("no usable trip updates feed this cycle", extra={"empty": saw_empty})
            return

        url, content, msg, ts = chosen
        ts = ts or int(now.timestamp())  # feed without header timestamp: use our clock
        if url != self._last_source:
            log.info("trip updates source", extra={"url": url, "previous": self._last_source})
            self._last_source = url
        if ts == self._last_ts["trip_updates"]:
            st.unchanged_skipped += 1
            return
        try:
            rows = trip_update_rows(msg, ts)
            if self.static_index:
                self.static_index.flag(rows)
            counts = Counter(flag for row in rows for flag in row["defect_flags"])
            dups = self.tu_store.ingest(now, rows)
            if dups:
                counts["DUP_KEY"] += dups
        except Exception:
            st.parse_errors += 1
            log.exception("trip updates processing failed")
            return
        self.stats.add_defects(counts)
        self._record_snapshot(st, "trip_updates", url, ts, content, now)

    def _poll_alerts(self, now: datetime) -> None:
        st = self.stats.alerts
        url = self.s.alerts_url
        if not url.strip():
            return
        res = self.fetcher.get(url)
        if res.status == "error":
            st.fetch_errors += 1
            log.warning("alerts fetch failed", extra={"url": url, "error": res.error})
            return
        if res.status == "not_modified":
            st.not_modified += 1
            return
        st.fetch_ok += 1
        try:
            msg = decode_feed(res.content)
            ts = header_timestamp(msg) or int(now.timestamp())
            if not msg.entity:
                st.empty += 1  # no active alert is a legitimate state
                return
            if ts == self._last_ts["alerts"]:
                st.unchanged_skipped += 1
                return
            self.alerts_store.ingest(now, alert_rows(msg, ts))
        except Exception as exc:
            st.parse_errors += 1
            log.error("alerts processing failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            return
        self._record_snapshot(st, "alerts", url, ts, res.content, now)

    def _record_snapshot(
        self, st: FeedStats, feed: str, url: str, ts: int, content: bytes, now: datetime
    ) -> None:
        self._last_ts[feed] = ts
        st.snapshot_ts.append(ts)
        st.sources[url] = st.sources.get(url, 0) + 1
        if not self.raw_paused and self.raw.due(feed, now):
            try:
                path = self.raw.save(feed, content, now)
                log.info("raw sample", extra={"file": path.name, "bytes": path.stat().st_size})
            except OSError as exc:
                log.error("raw sample failed", extra={"error": str(exc)})

    # ---- housekeeping ----------------------------------------------------------------------

    def _roll_day(self, now: datetime) -> None:
        today = paris_date(now).isoformat()
        if self.stats.day != today:
            save_stats(self.data_dir, self.stats)  # the finished day is reported later
            self.stats = load_stats(self.data_dir, paris_date(now))

    def _flush_all(self) -> None:
        for store in (self.tu_store, self.alerts_store):
            try:
                store.flush()
            except OSError as exc:  # disk full...: keep the state in memory, retry next time
                log.error("hourly flush failed", extra={"kind": store.kind, "error": str(exc)})
        save_stats(self.data_dir, self.stats)
        now = self._now()
        report = build_report(
            self.data_dir, self.stats, self.s.poll_seconds, complete=False, now=now
        )
        path = write_report(self.data_dir, report)
        log.info(
            "coverage report written",
            extra={
                "file": path.name,
                "bytes": path.stat().st_size,
                "polls_done": report["polls_done"],
                "polls_expected": report["polls_expected"],
                "snapshots": report["trip_updates"]["distinct_snapshots"],
                "rows_written": report["rows_written"],
            },
        )

    def _maybe_flush(self) -> None:
        if self._mono() - self._last_flush < self.s.flush_every_min * 60:
            return
        self._last_flush = self._mono()
        self._flush_all()
        result = enforce_retention(self.data_dir, self.s.max_local_mb, self.manifest)
        if result.blocked != self.raw_paused:
            self.raw_paused = result.blocked
            log.warning(
                "raw samples paused" if result.blocked else "raw samples resumed",
                extra={"used_mb": round(result.bytes_after / 2**20, 1)},
            )

    def _maybe_daily(self, now: datetime) -> None:
        if paris_hour(now) < self.s.upload_after_hour or now < self._next_daily:
            return
        today = paris_date(now)
        days = [
            d
            for d in pending_days(self.data_dir, today, self.manifest)
            if d not in self._done_dry_days
        ]
        failed = False
        for day in days:
            if not self._finish_day(day, now):
                failed = True
        self._next_daily = now + (RETRY_AFTER if failed else IDLE_CHECK)

    def _finish_day(self, day: date, now: datetime) -> bool:
        """Compact, report, upload one finished day. True when nothing is left to retry."""
        for kind in ("reduced", "alerts"):
            try:
                compact_day(self.data_dir, kind, day)
            except Exception:
                log.exception("compaction failed", extra={"kind": kind, "day": day.isoformat()})
                return False
        stats = load_stats(self.data_dir, day)
        report = build_report(self.data_dir, stats, self.s.poll_seconds, complete=True, now=now)
        write_report(self.data_dir, report)
        result = self.uploader.upload_day(day)
        if result.status == "uploaded":
            stats_path(self.data_dir, day).unlink(missing_ok=True)
        elif result.status == "dry_run":
            self._done_dry_days.add(day)
        return result.status != "failed"

    def _maybe_static(self, now: datetime) -> None:
        if now < self._next_static:
            return
        state = load_state(self.data_dir)
        today = paris_date(now).isoformat()
        due = "sha256" not in state or (
            paris_hour(now) >= self.s.static_check_after_hour and state.get("checked_date") != today
        )
        if not due:
            return
        res = refresh_static(self.data_dir, self.fetcher, self.s.static_gtfs_url, now)
        if res.status == "error":
            log.error("static GTFS refresh failed", extra={"error": res.error})
            self._next_static = now + RETRY_AFTER
        elif res.status == "changed":
            self.static_index = load_static_index(self.data_dir)

    def _heartbeat(self) -> None:
        payload = {"ts": int(self._now().timestamp()), "pid": os.getpid()}
        try:
            write_bytes_atomic(self.s.heartbeat_path, json.dumps(payload).encode())
        except OSError as exc:
            log.error("heartbeat write failed", extra={"error": str(exc)})
