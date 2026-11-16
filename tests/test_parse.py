import json
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2 as pb
from helpers import alert_entity, feed, stop, trip_entity

from tc.ingest.decode import FeedParseError, decode_feed, header_timestamp
from tc.ingest.parse_alerts import alert_rows
from tc.ingest.parse_tu import trip_update_rows, uic_from_stop_id

FIXTURES = Path(__file__).parent / "fixtures"
TU_FIELDS = {
    "feed_ts", "start_date", "trip_id", "route_id", "trip_schedule_relationship", "stop_id",
    "uic", "stop_sequence", "arrival_delay", "departure_delay", "arrival_time", "departure_time",
    "stop_schedule_relationship", "defect_flags",
}  # fmt: skip


# ---- real fixtures -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_tu():
    msg = decode_feed((FIXTURES / "trip_updates.pb").read_bytes())
    return msg, trip_update_rows(msg, header_timestamp(msg))


def test_real_trip_updates_parse_without_crash(real_tu):
    msg, rows = real_tu
    meta = json.loads((FIXTURES / "fixtures.json").read_text())
    assert header_timestamp(msg) == meta["trip_updates"]["feed_timestamp"]
    assert len(rows) >= len(msg.entity) > 0
    assert (FIXTURES / "trip_updates.pb").stat().st_size < 200_000
    for row in rows:
        assert set(row) == TU_FIELDS
        assert row["feed_ts"] == header_timestamp(msg)
        assert isinstance(row["defect_flags"], list)


def test_real_rows_carry_ids_and_8_digit_uic(real_tu):
    _, rows = real_tu
    with_stop = [r for r in rows if r["stop_id"]]
    assert with_stop
    with_uic = [r for r in with_stop if r["uic"]]
    assert all(len(r["uic"]) == 8 and r["uic"].isdigit() for r in with_uic)
    assert all(r["stop_id"].endswith(r["uic"]) for r in with_uic)
    # A stop without a UIC must be flagged, never silently dropped.
    assert all("NO_UIC" in r["defect_flags"] for r in with_stop if not r["uic"])
    assert len(with_uic) / len(with_stop) > 0.5


def test_real_delays_and_times_are_ints_or_none(real_tu):
    _, rows = real_tu
    for r in rows:
        for k in ("arrival_delay", "departure_delay", "arrival_time", "departure_time"):
            assert r[k] is None or isinstance(r[k], int)
    assert any(r["arrival_delay"] is not None or r["departure_delay"] is not None for r in rows)


def test_real_alerts_parse():
    msg = decode_feed((FIXTURES / "alerts.pb").read_bytes())
    rows = alert_rows(msg, header_timestamp(msg))
    assert len(rows) == len(msg.entity) > 0
    assert all(r["alert_id"] for r in rows)
    assert all(isinstance(r["active_periods"], list) for r in rows)
    assert all(isinstance(r["informed_entities"], list) for r in rows)
    assert any(r["header_text"] for r in rows)


# ---- synthetic defects ---------------------------------------------------------------------


def test_uic_extraction():
    assert uic_from_stop_id("StopPoint:OCETGV INOUI-87391003") == "87391003"
    assert uic_from_stop_id("StopPoint:OCETrain TER-87212027") == "87212027"
    assert uic_from_stop_id("StopPoint:X-1234567") is None  # 7 digits
    assert uic_from_stop_id("StopPoint:X-123456789") is None  # 9 digits: not a UIC
    assert uic_from_stop_id("") is None and uic_from_stop_id(None) is None


def test_row_values_delays_times_and_relationships():
    msg = feed()
    trip_entity(
        msg,
        trip_id="OCESN877703F4849586:2025-07-10T23:16:05Z",
        stops=[
            stop("StopPoint:OCETGV INOUI-87391003", 1, a_delay=120, d_delay=180, a_time=1000,
                 d_time=1100),
            stop("StopPoint:OCETGV INOUI-87686006", 2, rel=pb.TripUpdate.StopTimeUpdate.SKIPPED),
        ],
    )  # fmt: skip
    rows = trip_update_rows(msg, 1_800_000_000)
    assert len(rows) == 2
    r0, r1 = rows
    assert (r0["uic"], r0["stop_sequence"]) == ("87391003", 1)
    assert (r0["arrival_delay"], r0["departure_delay"]) == (120, 180)
    assert (r0["arrival_time"], r0["departure_time"]) == (1000, 1100)
    assert r0["trip_schedule_relationship"] == "SCHEDULED"
    assert r0["stop_schedule_relationship"] == "SCHEDULED"
    assert r1["stop_schedule_relationship"] == "SKIPPED"
    assert r1["arrival_delay"] is None
    assert r0["defect_flags"] == [] and r1["defect_flags"] == []


def test_defects_are_flagged_and_rows_kept():
    msg = feed()
    trip_entity(
        msg,
        stops=[
            stop("StopPoint:A-87000001", 5),
            stop("StopPoint:B-87000002", 3),  # backwards: E002
            stop("StopPoint:C-87000003", 3),  # repeated sequence: E036
            stop("StopPoint:A-87000001", 9),  # same stop again: E009
            stop("WEIRD", 10),  # no UIC
            stop(None, 11),  # no stop id
        ],
    )
    rows = trip_update_rows(msg, 1)
    assert len(rows) == 6  # nothing dropped
    flags = [set(r["defect_flags"]) for r in rows]
    assert flags[0] == set()
    assert "UNSORTED_STOP_TIME_UPDATE" in flags[1]
    assert "DUP_STOP_SEQUENCE" in flags[2]
    assert "DUP_STOP_ID" in flags[3]
    assert "NO_UIC" in flags[4]
    assert "NO_STOP_ID" in flags[5]


def test_trip_without_stop_updates_or_id_is_kept_and_flagged():
    msg = feed()
    trip_entity(msg, trip_id="CANCELLED1", stops=[], rel=pb.TripDescriptor.CANCELED)
    trip_entity(msg, trip_id=None, stops=[stop("StopPoint:A-87000001", 1)])
    rows = trip_update_rows(msg, 1)
    assert rows[0]["trip_schedule_relationship"] == "CANCELED"
    assert rows[0]["defect_flags"] == ["NO_STOP_TIME_UPDATE"]
    assert rows[0]["stop_id"] is None
    assert rows[1]["trip_id"] is None and "MISSING_TRIP_ID" in rows[1]["defect_flags"]


def test_garbage_bytes_raise_a_typed_error_and_empty_body_is_an_empty_feed():
    with pytest.raises(FeedParseError):
        decode_feed(b"<html>502 Bad Gateway</html>")
    empty = decode_feed(b"")
    assert len(empty.entity) == 0 and header_timestamp(empty) == 0


def test_alerts_prefer_french_and_fall_back():
    msg = feed()
    alert_entity(msg, "A1", fr="Travaux en gare", en="Works", trips=["T1"], stops=["S1"])
    alert_entity(msg, "A2", fr=None, en="Only English")
    rows = alert_rows(msg, 7)
    assert rows[0]["header_text"] == "Travaux en gare"
    assert rows[1]["header_text"] == "Only English"
    assert rows[0]["description_text"] == "Description sans langue française"
    assert (rows[0]["cause"], rows[0]["effect"]) == ("CONSTRUCTION", "DETOUR")
    assert rows[0]["active_periods"] == [{"start": 1_800_000_000, "end": None}]
    assert rows[0]["informed_entities"] == [
        {"trip_id": "T1", "route_id": None, "stop_id": None},
        {"trip_id": None, "route_id": None, "stop_id": "S1"},
    ]
