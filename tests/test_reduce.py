from google.transit import gtfs_realtime_pb2 as pb
from helpers import feed, stop, trip_entity

from tc.ingest.parse_tu import trip_update_rows
from tc.ingest.reduce import Reducer
from tc.store.schema import TU_KEY


def _row(ts, delay, stop_id="StopPoint:A-87000001", seq=1, trip="T1", flags=()):
    return {
        "feed_ts": ts, "start_date": "20261116", "trip_id": trip, "route_id": "R",
        "trip_schedule_relationship": "SCHEDULED", "stop_id": stop_id, "uic": "87000001",
        "stop_sequence": seq, "arrival_delay": delay, "departure_delay": None,
        "arrival_time": None, "departure_time": None, "stop_schedule_relationship": "SCHEDULED",
        "defect_flags": list(flags),
    }  # fmt: skip


def test_latest_wins_and_aggregates():
    r = Reducer(TU_KEY)
    r.add_snapshot([_row(100, 60)])
    r.add_snapshot([_row(220, 180)])
    r.add_snapshot([_row(340, 300)])
    (row,) = r.rows()
    assert row["arrival_delay"] == 300 and row["feed_ts"] == 340
    assert (row["first_seen_ts"], row["last_seen_ts"], row["n_updates"]) == (100, 340, 3)


def test_out_of_order_snapshot_does_not_overwrite_newer_data():
    r = Reducer(TU_KEY)
    r.add_snapshot([_row(340, 300)])
    r.add_snapshot([_row(100, 60)])  # arrives late
    (row,) = r.rows()
    assert row["arrival_delay"] == 300 and row["feed_ts"] == 340  # newest payload kept
    assert row["first_seen_ts"] == 100  # but the history is extended
    assert (row["last_seen_ts"], row["n_updates"]) == (340, 2)


def test_distinct_keys_are_kept_apart():
    r = Reducer(TU_KEY)
    r.add_snapshot([_row(1, 0, seq=1), _row(1, 0, seq=2), _row(1, 0, seq=1, trip="T2")])
    assert len(r) == 3


def test_same_key_twice_in_one_snapshot_is_flagged_not_dropped_silently():
    r = Reducer(TU_KEY)
    dups = r.add_snapshot([_row(100, 60), _row(100, 90)])
    (row,) = r.rows()
    assert dups == 1
    assert row["arrival_delay"] == 90  # last in feed order wins
    assert "DUP_KEY" in row["defect_flags"]
    assert row["n_updates"] == 1  # one snapshot = one update


def test_defect_flags_from_the_parser_survive_reduction():
    msg = feed(ts=500)
    trip_entity(msg, stops=[stop("StopPoint:A-87000001", 5), stop("StopPoint:B-87000002", 3)])
    trip_entity(msg, trip_id="NOSTOPS", stops=[], rel=pb.TripDescriptor.CANCELED)
    r = Reducer(TU_KEY)
    r.add_snapshot(trip_update_rows(msg, 500))
    by_stop = {row["stop_id"]: row for row in r.rows()}
    assert by_stop["StopPoint:B-87000002"]["defect_flags"] == ["UNSORTED_STOP_TIME_UPDATE"]
    assert by_stop[None]["trip_schedule_relationship"] == "CANCELED"
    assert by_stop[None]["defect_flags"] == ["NO_STOP_TIME_UPDATE"]


def test_reducing_a_real_looking_stream_is_idempotent_on_keys():
    msg = feed(ts=500)
    trip_entity(msg, stops=[stop("StopPoint:A-87000001", 1, a_delay=60)])
    rows = trip_update_rows(msg, 500)
    r = Reducer(TU_KEY)
    r.add_snapshot(rows)
    r.add_snapshot(trip_update_rows(msg, 620))
    assert len(r) == 1 and r.rows()[0]["n_updates"] == 2


def test_load_restores_state_and_continues_counting():
    r = Reducer(TU_KEY)
    r.add_snapshot([_row(100, 60)])
    saved = r.rows()
    r2 = Reducer(TU_KEY)
    r2.load(saved)
    r2.add_snapshot([_row(220, 90)])
    (row,) = r2.rows()
    assert (row["n_updates"], row["first_seen_ts"], row["arrival_delay"]) == (2, 100, 90)
