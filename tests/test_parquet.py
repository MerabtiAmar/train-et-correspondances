from datetime import UTC, date, datetime

import pyarrow.parquet as pq
import pytest

from tc.store import parquet as store
from tc.store.parquet import HourlyStore, compact_day, hourly_path
from tc.store.schema import ALERTS_SCHEMA, TU_SCHEMA, rows_to_table, table_to_rows

H10 = datetime(2026, 11, 16, 10, 5, tzinfo=UTC)
H11 = datetime(2026, 11, 16, 11, 5, tzinfo=UTC)


def _tu(ts, delay, seq=1, trip="T1", flags=()):
    return {
        "feed_ts": ts, "start_date": "20261116", "trip_id": trip, "route_id": "R",
        "trip_schedule_relationship": "SCHEDULED", "stop_id": f"StopPoint:A-8700000{seq}",
        "uic": f"8700000{seq}", "stop_sequence": seq, "arrival_delay": delay,
        "departure_delay": None, "arrival_time": ts + 60, "departure_time": None,
        "stop_schedule_relationship": "SCHEDULED", "defect_flags": list(flags),
    }  # fmt: skip


def _read(path):
    return pq.read_table(path).to_pylist()


def test_rows_roundtrip_through_arrow():
    rows = [{**_tu(1_800_000_000, 120), "first_seen_ts": 1, "last_seen_ts": 2, "n_updates": 3}]
    back = table_to_rows(rows_to_table(rows, TU_SCHEMA))
    assert back == rows

    alert = {
        "feed_ts": 5, "alert_id": "A", "cause": "STRIKE", "effect": "NO_SERVICE",
        "active_periods": [{"start": 10, "end": None}],
        "informed_entities": [{"trip_id": "T", "route_id": None, "stop_id": None}],
        "header_text": "Grève", "description_text": None,
        "first_seen_ts": 5, "last_seen_ts": 5, "n_updates": 1,
    }  # fmt: skip
    assert table_to_rows(rows_to_table([alert], ALERTS_SCHEMA)) == [alert]


def test_hourly_flush_writes_one_file_and_rewrites_it(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    path = s.flush()
    assert path == hourly_path(tmp_path, "reduced", H10)
    assert path.parent.name == "date=2026-11-16" and path.name == "20261116T10Z.parquet"
    assert len(_read(path)) == 1

    s.ingest(H10, [_tu(220, 90), _tu(220, 5, seq=2)])
    s.flush()
    rows = sorted(_read(path), key=lambda r: r["stop_sequence"])
    assert [r["arrival_delay"] for r in rows] == [90, 5]
    assert rows[0]["n_updates"] == 2
    assert not list(tmp_path.rglob("*.tmp"))  # no temp file left behind


def test_flush_without_changes_does_not_touch_the_file(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    path = s.flush()
    mtime = path.stat().st_mtime_ns
    assert s.flush() is None
    assert path.stat().st_mtime_ns == mtime


def test_hour_rollover_flushes_previous_hour(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    s.ingest(H11, [_tu(3700, 90)])  # new hour: previous one must be on disk already
    assert hourly_path(tmp_path, "reduced", H10).exists()
    assert not hourly_path(tmp_path, "reduced", H11).exists()  # not flushed yet
    s.flush()
    assert hourly_path(tmp_path, "reduced", H11).exists()


def test_restart_resumes_the_current_hour_instead_of_overwriting_it(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60, seq=1)])
    s.flush()
    # process restarts: fresh store, same hour
    s2 = HourlyStore(tmp_path, "reduced")
    s2.ingest(H10, [_tu(220, 90, seq=2), _tu(220, 70, seq=1)])
    path = s2.flush()
    rows = {r["stop_sequence"]: r for r in _read(path)}
    assert set(rows) == {1, 2}  # earlier part of the hour survived
    assert rows[1]["arrival_delay"] == 70 and rows[1]["n_updates"] == 2
    assert rows[1]["first_seen_ts"].timestamp() == 100


def test_failed_write_keeps_previous_file_intact(tmp_path, monkeypatch):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    path = s.flush()
    before = path.read_bytes()
    s.ingest(H10, [_tu(220, 90)])

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store.pq, "write_table", boom)
    with pytest.raises(OSError):
        s.flush()
    assert path.read_bytes() == before  # old file untouched
    assert not list(tmp_path.rglob("*.tmp"))
    assert s.dirty  # will be retried on the next flush


def test_cleanup_removes_orphan_temp_files(tmp_path):
    orphan = tmp_path / "reduced" / "date=2026-11-16" / ".20261116T10Z.parquet.tmp"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"partial")
    assert store.cleanup_tmp(tmp_path) == 1
    assert not orphan.exists()


def test_daily_compaction_latest_wins_and_merges_aggregates(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    # same (trip, stop) seen in two consecutive hours, plus one key only in the first hour
    s.ingest(H10, [_tu(100, 60), _tu(100, 10, seq=2)])
    s.ingest(H10, [_tu(220, 80)])
    s.ingest(H11, [_tu(3700, 300)])
    s.flush()
    folder = tmp_path / "reduced" / "date=2026-11-16"
    assert len(list(folder.glob("2*.parquet"))) == 2

    out = compact_day(tmp_path, "reduced", date(2026, 11, 16))
    assert out == folder / "day.parquet"
    assert sorted(p.name for p in folder.iterdir()) == ["day.parquet"]  # hourly files merged away
    rows = {r["stop_sequence"]: r for r in _read(out)}
    assert rows[1]["arrival_delay"] == 300  # latest feed_ts wins across hours
    assert rows[1]["n_updates"] == 3  # 2 in hour 10 + 1 in hour 11
    assert rows[1]["first_seen_ts"].timestamp() == 100
    assert rows[1]["last_seen_ts"].timestamp() == 3700
    assert rows[2]["arrival_delay"] == 10 and rows[2]["n_updates"] == 1


def test_compaction_is_rerunnable_and_nothing_to_do_returns_none(tmp_path):
    assert compact_day(tmp_path, "reduced", date(2026, 11, 16)) is None
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    s.flush()
    compact_day(tmp_path, "reduced", date(2026, 11, 16))
    # a straggler hourly file appears afterwards: it is merged into the existing day file
    s2 = HourlyStore(tmp_path, "reduced")
    s2.ingest(H11, [_tu(3700, 90)])
    s2.flush()
    out = compact_day(tmp_path, "reduced", date(2026, 11, 16))
    (row,) = _read(out)
    assert row["arrival_delay"] == 90 and row["n_updates"] == 2


def test_compaction_merges_null_keys_of_cancelled_trips(tmp_path):
    cancelled = {
        **_tu(100, None),
        "stop_id": None, "uic": None, "stop_sequence": None, "arrival_time": None,
        "trip_schedule_relationship": "CANCELED", "defect_flags": ["NO_STOP_TIME_UPDATE"],
    }  # fmt: skip
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [cancelled])
    s.ingest(H11, [{**cancelled, "feed_ts": 3700}])
    s.flush()
    (row,) = _read(compact_day(tmp_path, "reduced", date(2026, 11, 16)))
    assert row["stop_id"] is None and row["stop_sequence"] is None
    assert row["n_updates"] == 2 and row["feed_ts"].timestamp() == 3700


def test_compaction_tie_on_feed_ts_goes_to_the_later_file(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(500, 60)])
    s.ingest(H11, [_tu(500, 90)])  # same feed timestamp seen again in the next hour
    s.flush()
    (row,) = _read(compact_day(tmp_path, "reduced", date(2026, 11, 16)))
    assert row["arrival_delay"] == 90 and row["n_updates"] == 2


def test_compaction_keeps_schema_types(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [_tu(100, 60)])
    s.ingest(H11, [_tu(3700, 90, seq=2)])
    s.flush()
    out = compact_day(tmp_path, "reduced", date(2026, 11, 16))
    schema = pq.read_schema(out)
    assert schema.names == TU_SCHEMA.names
    for field in TU_SCHEMA:
        got = schema.field(field.name).type
        if field.type == TU_SCHEMA.field("feed_ts").type:
            assert str(got).startswith("timestamp[") and "UTC" in str(got), field.name
        else:
            assert got == field.type, field.name


def test_alerts_compaction_with_nested_columns(tmp_path):
    def alert(ts, header):
        return {
            "feed_ts": ts, "alert_id": "A1", "cause": "STRIKE", "effect": "NO_SERVICE",
            "active_periods": [{"start": ts, "end": None}],
            "informed_entities": [{"trip_id": "T", "route_id": None, "stop_id": None}],
            "header_text": header, "description_text": None,
        }  # fmt: skip

    s = HourlyStore(tmp_path, "alerts")
    s.ingest(H10, [alert(100, "Grève annoncée")])
    s.ingest(H11, [alert(3700, "Grève confirmée")])
    s.flush()
    (row,) = _read(compact_day(tmp_path, "alerts", date(2026, 11, 16)))
    assert row["header_text"] == "Grève confirmée" and row["n_updates"] == 2
    assert row["active_periods"][0]["start"].timestamp() == 3700


def test_compaction_of_an_empty_hour_file(tmp_path):
    s = HourlyStore(tmp_path, "reduced")
    s.ingest(H10, [])  # a snapshot without any trip update still marks the hour
    s.flush()
    out = compact_day(tmp_path, "reduced", date(2026, 11, 16))
    assert pq.read_table(out).num_rows == 0


def test_paris_date_partition_for_late_utc_hour(tmp_path):
    late = datetime(2026, 11, 16, 23, 10, tzinfo=UTC)  # already the 17th in Paris
    assert hourly_path(tmp_path, "reduced", late).parent.name == "date=2026-11-17"
