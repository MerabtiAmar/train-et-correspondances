import json
from datetime import UTC, date, datetime, timedelta

import pyarrow.parquet as pq
from helpers import alert_entity, feed, stop, trip_entity
from test_static_raw import make_zip
from test_upload_retention_coverage import FakeHub

from tc.collector import Collector
from tc.common.config import Settings
from tc.health import check
from tc.ingest.fetch import FetchError, FetchResult
from tc.main import run
from tc.store.manifest import Manifest
from tc.store.parquet import hourly_path
from tc.store.upload import Uploader

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
TS0 = int(NOW.timestamp())
URL_A = "https://feeds.test/tu-primary"
URL_B = "https://feeds.test/tu-fallback"
URL_AL = "https://feeds.test/alerts"


class Clock:
    def __init__(self, start=NOW):
        self.now = start
        self.mono = 0.0

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


class FakeFetcher:
    """Serves queued FetchResults per URL (the last one repeats)."""

    def __init__(self, zip_bytes=None):
        self.queues: dict[str, list[FetchResult]] = {}
        self.calls: list[str] = []
        self.zip_bytes = zip_bytes

    def serve(self, url, *results):
        self.queues[url] = list(results)

    def get(self, url, conditional=True):
        self.calls.append(url)
        queue = self.queues.get(url)
        if not queue:
            return FetchResult(url, "error", error="no route")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def download(self, url, dest):
        if self.zip_bytes is None:
            raise FetchError("offline")
        import hashlib

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.zip_bytes)
        return hashlib.sha256(self.zip_bytes).hexdigest(), len(self.zip_bytes)

    def close(self):
        pass


def ok(url, msg):
    return FetchResult(url, "ok", content=msg.SerializeToString())


def tu_feed(ts, delay=60, seq=1, trip="T1"):
    msg = feed(ts)
    trip_entity(msg, trip_id=trip, stops=[stop(f"StopPoint:A-8700000{seq}", seq, a_delay=delay)])
    return msg


def mk(tmp_path, fetcher, clock, uploader=None, **kw):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        trip_updates_url=URL_A,
        trip_updates_url_fallback=URL_B,
        alerts_url=URL_AL,
        **kw,
    )
    return settings, Collector(
        settings, fetcher, uploader=uploader, now=lambda: clock.now, monotonic=lambda: clock.mono
    )


def rows_of(path):
    return pq.read_table(path).to_pylist()


# ---- normal operation ----------------------------------------------------------------------


def test_cycles_reduce_flush_and_report(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0, 60)), ok(URL_A, tu_feed(TS0 + 120, 90)))
    al = feed(TS0)
    alert_entity(al, "A1")
    f.serve(URL_AL, ok(URL_AL, al))

    c.poll_once()
    clock.advance(120)
    c.poll_once()
    clock.advance(120)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 120, 90)))  # same header timestamp again
    c.poll_once()

    c.shutdown()
    (row,) = rows_of(hourly_path(tmp_path, "reduced", NOW))
    assert row["arrival_delay"] == 90 and row["n_updates"] == 2
    (alert,) = rows_of(hourly_path(tmp_path, "alerts", NOW))
    assert alert["alert_id"] == "A1"

    tu = c.stats.trip_updates
    assert (c.stats.polls_done, tu.fetch_ok, tu.unchanged_skipped) == (3, 3, 1)
    assert sorted(tu.snapshot_ts) == [TS0, TS0 + 120]
    assert c.stats.alerts.unchanged_skipped == 2  # same alert feed timestamp each time

    report = json.loads((tmp_path / "coverage" / "date=2026-11-16" / "coverage.json").read_text())
    assert report["complete"] is False and report["polls_done"] == 3
    assert report["rows_written"]["reduced"] == 1
    assert check(Settings(_env_file=None, data_dir=tmp_path), now=clock.now.timestamp())[0]


def test_one_raw_sample_per_interval(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock, raw_sample_every_min=60)
    for i in range(3):
        f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 120 * i)))
        c.poll_once()
        clock.advance(120)
    raw = list((tmp_path / "raw").rglob("*.pb.gz"))
    assert len([p for p in raw if p.name.startswith("trip_updates_")]) == 1


def test_defects_are_counted_and_rows_still_written(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    msg = feed(TS0)
    trip_entity(msg, stops=[stop("StopPoint:A-87000001", 5), stop("StopPoint:B-87000002", 3)])
    trip_entity(msg, trip_id="X", stops=[])
    f.serve(URL_A, ok(URL_A, msg))
    c.poll_once()
    c.shutdown()
    assert c.stats.defects == {"UNSORTED_STOP_TIME_UPDATE": 1, "NO_STOP_TIME_UPDATE": 1}
    assert len(rows_of(hourly_path(tmp_path, "reduced", NOW))) == 3


# ---- bad feeds are a normal state ------------------------------------------------------------


def test_fallback_is_used_when_primary_is_empty(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, feed(TS0)))  # valid but empty
    f.serve(URL_B, ok(URL_B, tu_feed(TS0)))
    c.poll_once()
    tu = c.stats.trip_updates
    assert tu.sources == {URL_B: 1} and tu.empty == 0
    assert f.calls.count(URL_A) == 1 and f.calls.count(URL_B) == 1  # one request per feed


def test_primary_is_not_bothered_twice_nor_fallback_when_primary_is_fine(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0)))
    c.poll_once()
    assert URL_B not in f.calls


def test_empty_everywhere_is_counted_and_polling_continues(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, feed(TS0)))
    f.serve(URL_B, ok(URL_B, feed(TS0)))
    c.poll_once()
    clock.advance(120)
    c.poll_once()
    assert c.stats.trip_updates.empty == 2 and c.stats.polls_done == 2
    assert not list((tmp_path / "reduced").rglob("*.parquet"))


def test_stale_feed_is_counted_and_still_archived_when_it_is_the_only_one(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    old = TS0 - 3600
    f.serve(URL_A, ok(URL_A, tu_feed(old)))
    f.serve(URL_B, FetchResult(URL_B, "error", error="HTTP 500"))
    c.poll_once()
    tu = c.stats.trip_updates
    assert tu.stale == 1 and tu.fetch_errors == 1 and tu.snapshot_ts == [old]


def test_stale_primary_loses_to_a_fresh_fallback(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 - 3600)))
    f.serve(URL_B, ok(URL_B, tu_feed(TS0)))
    c.poll_once()
    assert c.stats.trip_updates.sources == {URL_B: 1} and c.stats.trip_updates.stale == 0


def test_garbage_and_http_errors_never_stop_the_collector(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, FetchResult(URL_A, "ok", content=b"<html>Bad gateway</html>"))
    f.serve(URL_B, FetchResult(URL_B, "error", error="HTTP 503"))
    f.serve(URL_AL, FetchResult(URL_AL, "ok", content=b"\xff\xfe garbage"))
    c.poll_once()
    assert c.stats.trip_updates.parse_errors == 1 and c.stats.trip_updates.fetch_errors == 1
    assert c.stats.alerts.parse_errors == 1
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 120)))
    clock.advance(120)
    c.poll_once()  # recovers by itself
    assert c.stats.trip_updates.snapshot_ts == [TS0 + 120]


def test_restart_resumes_the_hour(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c1 = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0, seq=1)))
    c1.poll_once()
    c1.shutdown()
    clock.advance(120)
    _, c2 = mk(tmp_path, f, clock)  # new process
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 120, seq=2)))
    c2.poll_once()
    c2.shutdown()
    rows = rows_of(hourly_path(tmp_path, "reduced", NOW))
    assert sorted(r["stop_sequence"] for r in rows) == [1, 2]
    assert c2.stats.polls_done == 2  # stats resumed too


# ---- daily job -----------------------------------------------------------------------------


def run_day_then_next_morning(tmp_path, uploader_factory):
    clock, f = Clock(), FakeFetcher()
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        trip_updates_url=URL_A,
        trip_updates_url_fallback=URL_B,
        alerts_url=URL_AL,
        hf_repo="me/ds",
    )
    uploader = uploader_factory(settings)
    c = Collector(
        settings, f, uploader=uploader, now=lambda: clock.now, monotonic=lambda: clock.mono
    )
    f.serve(URL_A, ok(URL_A, tu_feed(TS0, 60)))
    c.poll_once()
    clock.advance(3700)  # next hour: previous hour file gets written on rollover
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 3700, 120)))
    c.poll_once()
    c.shutdown()
    # next morning, 03:30 Paris
    clock.now = datetime(2026, 11, 17, 2, 30, tzinfo=UTC)
    clock.mono += 3600 * 16
    f.serve(URL_A, ok(URL_A, tu_feed(int(clock.now.timestamp()), 5)))
    c.poll_once()
    return c


def test_daily_job_compacts_reports_uploads_and_frees_disk(tmp_path):
    hub = FakeHub()
    c = run_day_then_next_morning(tmp_path, lambda s: Uploader(s, Manifest(tmp_path), client=hub))
    paths = set(hub.files)
    assert "reduced/date=2026-11-16/day.parquet" in paths
    assert "coverage/date=2026-11-16/coverage.json" in paths
    assert "README.md" in paths
    assert not (tmp_path / "reduced" / "date=2026-11-16").exists()  # local copy freed
    assert (tmp_path / "reduced" / "date=2026-11-17").exists()  # today's data untouched
    report = json.loads(hub.files["coverage/date=2026-11-16/coverage.json"])
    assert report["complete"] is True and report["polls_done"] == 2
    assert report["rows_written"]["reduced"] == 1  # one key, merged across two hours
    assert not (tmp_path / ".state" / "stats-2026-11-16.json").exists()
    assert c.stats.day == "2026-11-17"


def test_without_token_the_day_is_kept_and_compacted(tmp_path):
    c = run_day_then_next_morning(tmp_path, lambda s: Uploader(s, Manifest(tmp_path)))
    day_dir = tmp_path / "reduced" / "date=2026-11-16"
    assert [p.name for p in day_dir.iterdir()] == ["day.parquet"]
    assert (tmp_path / "coverage" / "date=2026-11-16" / "coverage.json").exists()
    assert date(2026, 11, 16) in c._done_dry_days


def test_failed_upload_keeps_files_and_retries_later(tmp_path):
    hub = FakeHub(fail_commit=True)
    c = run_day_then_next_morning(tmp_path, lambda s: Uploader(s, Manifest(tmp_path), client=hub))
    assert (tmp_path / "reduced" / "date=2026-11-16" / "day.parquet").exists()
    hub.fail_commit = False
    c._now = lambda: datetime(2026, 11, 17, 4, 0, tzinfo=UTC)  # an hour later: retry is due
    c._maybe_daily(c._now())
    assert "reduced/date=2026-11-16/day.parquet" in hub.files
    assert not (tmp_path / "reduced" / "date=2026-11-16").exists()


# ---- disk cap, static, loop ----------------------------------------------------------------


def test_raw_samples_pause_when_disk_cap_is_hit_with_nothing_uploadable(tmp_path):
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock, max_local_mb=0)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0)))
    c.poll_once()
    clock.advance(10 * 60 + 1)  # flush interval elapsed
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 601)))
    c.poll_once()
    assert c.raw_paused
    before = len(list((tmp_path / "raw").rglob("*.pb.gz")))
    clock.advance(3600)  # a new sampling interval, but samples are paused
    f.serve(URL_A, ok(URL_A, tu_feed(TS0 + 4201)))
    c.poll_once()
    assert len(list((tmp_path / "raw").rglob("*.pb.gz"))) == before
    assert list((tmp_path / "reduced").rglob("*.parquet"))  # reduced data still written


def test_static_refresh_runs_once_and_feeds_the_defect_index(tmp_path):
    clock, f = Clock(), FakeFetcher(zip_bytes=make_zip())
    _, c = mk(tmp_path, f, clock)
    f.serve(URL_A, ok(URL_A, tu_feed(TS0)))
    c.poll_once()  # first run: static is downloaded because none exists yet
    assert list((tmp_path / "static").rglob("trips.parquet"))
    assert c.static_index is not None
    assert c.stats.defects == {}  # snapshot 1 was processed before the index existed

    msg = feed(TS0 + 120)  # make_zip() knows trip T1 and stop A-87000001 only
    trip_entity(msg, trip_id="T1", stops=[stop("StopPoint:A-87000001", 1)])
    trip_entity(msg, trip_id="NOT-IN-GTFS", stops=[stop("StopPoint:Z-87999999", 1)])
    clock.advance(120)
    f.serve(URL_A, ok(URL_A, msg))
    c.poll_once()
    c.shutdown()
    flags = {
        r["trip_id"]: r["defect_flags"] for r in rows_of(hourly_path(tmp_path, "reduced", NOW))
    }
    assert flags["T1"] == []
    assert flags["NOT-IN-GTFS"] == ["TRIP_NOT_IN_STATIC", "STOP_NOT_IN_STATIC"]
    assert c.stats.defects == {"TRIP_NOT_IN_STATIC": 1, "STOP_NOT_IN_STATIC": 1}


def test_main_loop_runs_for_n_minutes_then_stops(tmp_path):
    clock = Clock()

    class StopFlag:
        def __init__(self):
            self.flag = False

        def is_set(self):
            return self.flag

        def wait(self, timeout):
            clock.mono += timeout
            return self.flag

    class FakeCollector:
        polls = 0
        skipped = 0

        def poll_once(self):
            FakeCollector.polls += 1
            if FakeCollector.polls == 3:
                raise RuntimeError("boom")  # a failing cycle must not stop the loop

        def note_overrun(self, n):
            FakeCollector.skipped += n

    settings = Settings(_env_file=None, data_dir=tmp_path, poll_seconds=120, run_for_minutes=10)
    run(settings, FakeCollector(), StopFlag(), monotonic=lambda: clock.mono)
    assert FakeCollector.polls == 5  # t = 0, 120, 240, 360, 480; stops at 600
    assert clock.mono == 600.0 and FakeCollector.skipped == 0


def test_main_loop_stops_promptly_on_signal(tmp_path):
    clock = Clock()

    class StopFlag:
        flag = False

        def is_set(self):
            return self.flag

        def wait(self, timeout):
            clock.mono += timeout
            return self.flag

    stop_flag = StopFlag()

    class FakeCollector:
        polls = 0

        def poll_once(self):
            FakeCollector.polls += 1
            stop_flag.flag = True  # SIGTERM arrives during the cycle

        def note_overrun(self, n):
            pass

    settings = Settings(_env_file=None, data_dir=tmp_path, poll_seconds=120)
    run(settings, FakeCollector(), stop_flag, monotonic=lambda: clock.mono)
    assert FakeCollector.polls == 1


def test_health_fails_without_or_with_old_heartbeat(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert check(s)[0] is False  # no heartbeat yet
    clock, f = Clock(), FakeFetcher()
    _, c = mk(tmp_path, f, clock)
    t = clock.now.timestamp()
    assert check(s, now=t + 599)[0] is True
    ok_, msg = check(s, now=t + 601)
    assert ok_ is False and "old" in msg
    del c
