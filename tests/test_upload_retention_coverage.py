import json
from collections import Counter
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from tc.common.config import Settings
from tc.store.coverage import (
    DayStats,
    build_report,
    find_gaps,
    load_stats,
    save_stats,
    write_report,
)
from tc.store.manifest import Manifest, day_files, pending_days
from tc.store.retention import enforce_retention
from tc.store.upload import Uploader

DAY = date(2026, 11, 15)
TODAY = date(2026, 11, 16)


class FakeHub:
    """Stands in for huggingface_hub.HfApi: stores committed files in memory."""

    def __init__(self, fail_commit=False, forget=()):
        self.files: dict[str, bytes] = {}
        self.fail_commit = fail_commit
        self.forget = set(forget)  # paths the "hub" silently fails to keep
        self.commits: list[str] = []

    def create_commit(self, repo_id, repo_type, operations, commit_message):
        if self.fail_commit:
            raise ConnectionError("hub unreachable")
        assert repo_type == "dataset"
        self.commits.append(commit_message)
        for op in operations:
            src = op.path_or_fileobj
            data = src if isinstance(src, bytes) else open(src, "rb").read()  # noqa: SIM115
            if op.path_in_repo not in self.forget:
                self.files[op.path_in_repo] = data

    def get_paths_info(self, repo_id, paths, repo_type):
        return [SimpleNamespace(path=p, size=len(self.files[p])) for p in paths if p in self.files]


def put(data_dir, rel, size=100, content=None):
    path = data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else b"x" * size)
    return path


def make_day(data_dir, day="2026-11-15"):
    return [
        put(data_dir, f"reduced/date={day}/day.parquet", 300),
        put(data_dir, f"alerts/date={day}/day.parquet", 50),
        put(data_dir, f"raw/date={day}/tu_20261115T100000Z.pb.gz", 200),
        put(data_dir, f"coverage/date={day}/coverage.json", 20),
    ]


def settings(tmp_path, **kw):
    return Settings(_env_file=None, data_dir=tmp_path, **kw)


# ---- upload --------------------------------------------------------------------------------


def test_dry_run_without_token_keeps_everything(tmp_path):
    files = make_day(tmp_path)
    up = Uploader(settings(tmp_path, hf_repo="me/ds"), Manifest(tmp_path))
    assert up.dry_run
    res = up.upload_day(DAY)
    assert res.status == "dry_run" and res.files == 4
    assert all(p.exists() for p in files)


def test_confirmed_upload_pushes_card_and_deletes_local_copy(tmp_path):
    files = make_day(tmp_path)
    hub, manifest = FakeHub(), Manifest(tmp_path)
    up = Uploader(settings(tmp_path, hf_repo="me/ds"), manifest, client=hub)
    res = up.upload_day(DAY)
    assert res.status == "uploaded"
    assert set(hub.files) == {
        "reduced/date=2026-11-15/day.parquet",
        "alerts/date=2026-11-15/day.parquet",
        "raw/date=2026-11-15/tu_20261115T100000Z.pb.gz",
        "coverage/date=2026-11-15/coverage.json",
        "README.md",
    }
    card = hub.files["README.md"].decode()
    assert "Données SNCF Voyageurs, licence ODbL" in card and "license: odbl" in card
    assert "me/ds" in card
    assert not any(p.exists() for p in files)  # local copy freed
    assert not (tmp_path / "reduced" / "date=2026-11-15").exists()  # empty folder removed
    assert json.loads((tmp_path / ".state" / "uploaded.json").read_text())  # recorded


def test_failed_upload_keeps_local_files_and_reports(tmp_path):
    files = make_day(tmp_path)
    up = Uploader(
        settings(tmp_path, hf_repo="me/ds"), Manifest(tmp_path), client=FakeHub(fail_commit=True)
    )
    res = up.upload_day(DAY)
    assert res.status == "failed" and "hub unreachable" in res.error
    assert all(p.exists() for p in files)
    assert pending_days(tmp_path, TODAY, Manifest(tmp_path)) == [DAY]  # will be retried


def test_unconfirmed_file_means_nothing_is_deleted(tmp_path):
    files = make_day(tmp_path)
    hub = FakeHub(forget={"alerts/date=2026-11-15/day.parquet"})
    up = Uploader(settings(tmp_path, hf_repo="me/ds"), Manifest(tmp_path), client=hub)
    res = up.upload_day(DAY)
    assert res.status == "failed" and "not confirmed" in res.error
    assert all(p.exists() for p in files)
    assert Manifest(tmp_path)._sizes == {}


def test_static_index_files_survive_upload(tmp_path):
    put(tmp_path, "static/date=2026-11-15/trips.parquet", 40)
    put(tmp_path, "static/date=2026-11-15/stop_times.parquet", 400)
    put(tmp_path, "static/date=2026-11-15/gtfs.zip", 100)
    up = Uploader(settings(tmp_path, hf_repo="me/ds"), Manifest(tmp_path), client=FakeHub())
    assert up.upload_day(DAY).status == "uploaded"
    left = sorted(p.name for p in (tmp_path / "static" / "date=2026-11-15").iterdir())
    assert left == ["trips.parquet"]


def test_nothing_to_upload(tmp_path):
    up = Uploader(settings(tmp_path, hf_repo="me/ds"), Manifest(tmp_path), client=FakeHub())
    assert up.upload_day(DAY).status == "nothing"


def test_pending_days_ignores_today_and_uploaded_days(tmp_path):
    make_day(tmp_path, "2026-11-14")
    make_day(tmp_path, "2026-11-16")  # today: still being written
    manifest = Manifest(tmp_path)
    assert pending_days(tmp_path, TODAY, manifest) == [date(2026, 11, 14)]
    for p in day_files(tmp_path, date(2026, 11, 14)):
        manifest.add(p, p.stat().st_size)
    assert pending_days(tmp_path, TODAY, manifest) == []


# ---- retention -----------------------------------------------------------------------------


def mark_uploaded(manifest, *paths):
    for p in paths:
        manifest.add(p, p.stat().st_size)


def test_retention_deletes_uploaded_raw_first_then_oldest(tmp_path):
    chunk = 629_145  # 0.6 MB: four files = 2.4 MB, so the 1 MB granularity of the cap matters
    raw_old = put(tmp_path, "raw/date=2026-11-10/a.pb.gz", chunk)
    raw_new = put(tmp_path, "raw/date=2026-11-14/b.pb.gz", chunk)
    red_old = put(tmp_path, "reduced/date=2026-11-09/day.parquet", chunk)  # oldest, but not raw
    red_new = put(tmp_path, "reduced/date=2026-11-14/day.parquet", chunk)
    manifest = Manifest(tmp_path)
    mark_uploaded(manifest, raw_old, raw_new, red_old, red_new)

    res = enforce_retention(tmp_path, max_mb=2, manifest=manifest)  # 2.4 MB: free one file
    assert res.deleted == ["raw/date=2026-11-10/a.pb.gz"]
    assert not raw_old.exists() and raw_new.exists() and red_old.exists()

    res = enforce_retention(tmp_path, max_mb=1, manifest=manifest)  # 1.8 MB: free two more
    assert res.deleted == ["raw/date=2026-11-14/b.pb.gz", "reduced/date=2026-11-09/day.parquet"]
    assert red_new.exists() and not res.blocked


def test_retention_never_deletes_data_that_is_not_uploaded(tmp_path):
    mb = 1024 * 1024
    unsent_raw = put(tmp_path, "raw/date=2026-11-10/a.pb.gz", mb)
    unsent_day = put(tmp_path, "reduced/date=2026-11-10/day.parquet", mb)
    res = enforce_retention(tmp_path, max_mb=1, manifest=Manifest(tmp_path))
    assert res.deleted == [] and res.blocked
    assert unsent_raw.exists() and unsent_day.exists()


def test_retention_ignores_a_rewritten_file_with_a_different_size(tmp_path):
    mb = 1024 * 1024
    path = put(tmp_path, "reduced/date=2026-11-10/day.parquet", mb)
    manifest = Manifest(tmp_path)
    mark_uploaded(manifest, path)
    put(tmp_path, "reduced/date=2026-11-10/day.parquet", 2 * mb)  # new content, not uploaded
    res = enforce_retention(tmp_path, max_mb=1, manifest=manifest)
    assert res.deleted == [] and res.blocked and path.exists()


def test_retention_noop_under_cap_and_never_touches_state(tmp_path):
    state = put(tmp_path, ".state/uploaded.json", 10)
    put(tmp_path, "raw/date=2026-11-10/a.pb.gz", 100)
    assert enforce_retention(tmp_path, max_mb=1, manifest=Manifest(tmp_path)).deleted == []
    manifest = Manifest(tmp_path)
    mark_uploaded(manifest, state)
    enforce_retention(tmp_path, max_mb=0, manifest=manifest)
    assert state.exists()


# ---- coverage ------------------------------------------------------------------------------


def test_find_gaps_over_six_minutes():
    ts = [1000, 1120, 1240, 1240, 1900, 2020]  # 1240 -> 1900 = 11 min hole
    gaps = find_gaps(ts, start=None, end=None)
    assert [g["minutes"] for g in gaps] == [11.0]
    assert find_gaps([1000, 1360], None, None) == []  # exactly 6 min is not a gap
    edges = find_gaps([1000, 1120], start=0, end=5000)
    assert [g["minutes"] for g in edges] == [round(1000 / 60, 1), round(3880 / 60, 1)]
    assert len(find_gaps([], start=0, end=3600)) == 1  # no snapshot at all


def test_stats_roundtrip_through_disk(tmp_path):
    stats = DayStats(day="2026-11-15", polls_done=5)
    stats.trip_updates.snapshot_ts += [1, 2]
    stats.trip_updates.sources["https://x"] = 2
    stats.add_defects(Counter({"E": 3}))
    save_stats(tmp_path, stats)
    assert load_stats(tmp_path, DAY) == stats
    assert load_stats(tmp_path, date(2026, 1, 1)).polls_done == 0  # missing = empty


def test_report_contents(tmp_path):
    day_start = int(
        datetime(2026, 11, 14, 23, 0, tzinfo=UTC).timestamp()
    )  # 00:00 Paris on the 15th
    stats = DayStats(day="2026-11-15", polls_done=3)
    stats.trip_updates.snapshot_ts = [day_start + 120 * i for i in range(3)]
    stats.trip_updates.empty = 1
    stats.trip_updates.stale = 2
    stats.trip_updates.parse_errors = 1
    stats.add_defects(Counter({"DUP_KEY": 4}))
    pq.write_table(
        pa.table({"a": [1, 2, 3]}),
        tmp_path / "reduced" / "date=2026-11-15" / "day.parquet"
        if (tmp_path / "reduced" / "date=2026-11-15").mkdir(parents=True) is None
        else None,
    )
    put(tmp_path, "raw/date=2026-11-15/tu.pb.gz", 77)

    now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
    report = build_report(tmp_path, stats, poll_seconds=120, complete=True, now=now)
    assert report["polls_expected"] == 720 and report["polls_done"] == 3
    assert report["trip_updates"]["distinct_snapshots"] == 3
    assert report["trip_updates"]["empty_feeds"] == 1
    assert report["trip_updates"]["stale_feeds"] == 2
    assert report["trip_updates"]["parse_errors"] == 1
    assert [g["minutes"] for g in report["trip_updates"]["gaps_over_6_min"]] == [1436.0]
    assert report["defects"] == {"DUP_KEY": 4}
    assert report["rows_written"] == {"reduced": 3, "alerts": 0}
    assert report["bytes_per_file"]["raw/date=2026-11-15/tu.pb.gz"] == 77
    path = write_report(tmp_path, report)
    assert path == tmp_path / "coverage" / "date=2026-11-15" / "coverage.json"
    assert json.loads(path.read_text())["day"] == "2026-11-15"
