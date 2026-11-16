import gzip
import io
import zipfile
from datetime import UTC, datetime

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from tc.common.config import Settings
from tc.ingest.fetch import Fetcher
from tc.ingest.static_gtfs import load_state, load_static_index, refresh_static
from tc.store.raw import RawSampler

URL = "https://example.test/gtfs.zip"
NOW = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)


def make_zip(extra_trip="T2", skip=(), bom=False) -> bytes:
    files = {
        "trips": f"route_id,service_id,trip_id,direction_id\nR1,S1,T1,0\nR1,S1,{extra_trip},1\n",
        "stop_times": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,25:10:00,25:12:00,StopPoint:A-87000001,0001\n"
            "T1,25:40:00,25:41:00,StopPoint:B-87000002,2\n"
        ),
        "stops": "stop_id,stop_name,stop_lat,stop_lon\nStopPoint:A-87000001,Gare A,48.1,2.3\n",
        "routes": "route_id,route_short_name,route_type\nR1,TGV,2\n",
        "calendar_dates": "service_id,date,exception_type\nS1,20261116,1\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            if name not in skip:
                zf.writestr(f"{name}.txt", ("﻿" if bom else "") + text)
    return buf.getvalue()


def fetcher_for(payload: bytes) -> Fetcher:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    )
    return Fetcher(Settings(_env_file=None), client=client, sleep=lambda s: None)


def test_first_download_converts_all_tables(tmp_path):
    res = refresh_static(tmp_path, fetcher_for(make_zip(bom=True)), URL, NOW)
    assert res.status == "changed"
    folder = tmp_path / "static" / "date=2026-11-16"
    assert res.folder == folder
    assert (folder / "gtfs.zip").exists()
    for name in ("trips", "stop_times", "stops", "routes", "calendar_dates"):
        assert (folder / f"{name}.parquet").exists()
    st = pq.read_table(folder / "stop_times.parquet")
    assert st.num_rows == 2
    assert st.schema.field("stop_sequence").type == pa.int32()
    assert st["stop_sequence"].to_pylist() == [1, 2]
    assert st["arrival_time"].to_pylist()[0] == "25:10:00"  # times > 24h stay text
    assert load_state(tmp_path)["sha256"] == res.sha256
    assert not list((tmp_path / "static").glob(".*"))  # no leftover download


def test_same_content_is_not_kept_twice(tmp_path):
    payload = make_zip()
    refresh_static(tmp_path, fetcher_for(payload), URL, NOW)
    later = datetime(2026, 11, 17, 5, 0, tzinfo=UTC)
    res = refresh_static(tmp_path, fetcher_for(payload), URL, later)
    assert res.status == "unchanged"
    assert not (tmp_path / "static" / "date=2026-11-17").exists()
    assert load_state(tmp_path)["checked_date"] == "2026-11-17"


def test_changed_content_creates_a_new_dated_folder(tmp_path):
    refresh_static(tmp_path, fetcher_for(make_zip()), URL, NOW)
    later = datetime(2026, 11, 18, 5, 0, tzinfo=UTC)
    res = refresh_static(tmp_path, fetcher_for(make_zip(extra_trip="T9")), URL, later)
    assert res.status == "changed"
    assert (tmp_path / "static" / "date=2026-11-18" / "trips.parquet").exists()


def test_bad_zip_is_an_error_and_keeps_previous_state(tmp_path):
    good = refresh_static(tmp_path, fetcher_for(make_zip()), URL, NOW)
    later = datetime(2026, 11, 17, 5, 0, tzinfo=UTC)
    res = refresh_static(tmp_path, fetcher_for(b"<html>not a zip</html>"), URL, later)
    assert res.status == "error" and "conversion failed" in res.error
    assert load_state(tmp_path)["sha256"] == good.sha256  # will retry later
    assert not list((tmp_path / "static").glob(".*"))


def test_missing_member_is_tolerated(tmp_path):
    res = refresh_static(tmp_path, fetcher_for(make_zip(skip=("calendar_dates",))), URL, NOW)
    assert res.status == "changed"
    assert not (res.folder / "calendar_dates.parquet").exists()


def test_download_failure_is_reported_not_raised(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    f = Fetcher(Settings(_env_file=None), client=client, sleep=lambda s: None)
    assert refresh_static(tmp_path, f, URL, NOW).status == "error"


def test_static_index_flags_unknown_trip_and_stop(tmp_path):
    assert load_static_index(tmp_path) is None
    refresh_static(tmp_path, fetcher_for(make_zip()), URL, NOW)
    index = load_static_index(tmp_path)
    rows = [
        {"trip_id": "T1", "stop_id": "StopPoint:A-87000001", "defect_flags": []},
        {"trip_id": "UNKNOWN", "stop_id": "StopPoint:Z-87999999", "defect_flags": []},
        {"trip_id": None, "stop_id": None, "defect_flags": []},
    ]
    index.flag(rows)
    assert rows[0]["defect_flags"] == []
    assert rows[1]["defect_flags"] == ["TRIP_NOT_IN_STATIC", "STOP_NOT_IN_STATIC"]
    assert rows[2]["defect_flags"] == []


def test_raw_sampler_saves_gzip_once_per_interval(tmp_path):
    s = RawSampler(tmp_path, every_min=60)
    t0 = datetime(2026, 11, 16, 10, 5, tzinfo=UTC)
    payload = b"\x0a\x04abcd" * 100
    assert s.due("tu", t0)
    path = s.save("tu", payload, t0)
    assert path.parent.name == "date=2026-11-16" and path.name == "tu_20261116T100500Z.pb.gz"
    assert gzip.decompress(path.read_bytes()) == payload
    assert not s.due("tu", datetime(2026, 11, 16, 10, 59, tzinfo=UTC))
    assert s.due("tu", datetime(2026, 11, 16, 11, 0, tzinfo=UTC))
    assert s.due("alerts", t0)  # independent per feed


def test_raw_sampler_restart_does_not_take_an_extra_sample(tmp_path):
    t0 = datetime(2026, 11, 16, 10, 5, tzinfo=UTC)
    RawSampler(tmp_path, every_min=60).save("trip_updates", b"x", t0)
    restarted = RawSampler(tmp_path, every_min=60)  # new process, same hour
    assert not restarted.due("trip_updates", datetime(2026, 11, 16, 10, 40, tzinfo=UTC))
    assert restarted.due("trip_updates", datetime(2026, 11, 16, 11, 0, tzinfo=UTC))
    assert restarted.due("alerts", t0)  # "alerts_" files are not mistaken for trip updates
