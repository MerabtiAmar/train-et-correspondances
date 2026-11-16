"""Trip Updates: one output row per (trip, stop_time_update). Never raises on data defects.

Defects are kept on the row as flags (see DEFECTS) and counted in the coverage report.
Timestamps are plain epoch seconds here; they become Arrow UTC timestamps when written.
"""

from __future__ import annotations

import re
from typing import Any

from google.transit import gtfs_realtime_pb2

from tc.ingest.decode import FeedMessage, enum_name

TripRel = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
StopRel = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship

# Last 8 digits of a stop_id are the UIC station code (`StopPoint:OCETGV INOUI-87391003`).
_UIC = re.compile(r"(?<!\d)(\d{8})$")

DEFECTS = {
    "MISSING_TRIP_ID": "trip descriptor without trip_id",
    "NO_STOP_TIME_UPDATE": "trip update without any stop_time_update (e.g. whole-trip cancel)",
    "NO_STOP_ID": "stop_time_update without stop_id",
    "NO_UIC": "stop_id does not end with 8 digits",
    "UNSORTED_STOP_TIME_UPDATE": "stop_sequence goes backwards inside a trip (E002)",
    "DUP_STOP_SEQUENCE": "stop_sequence repeated inside a trip (E036)",
    "DUP_STOP_ID": "same stop_id twice inside a trip (E009)",
    "DUP_KEY": "same reduction key twice in one snapshot (added by the reducer)",
    "TRIP_NOT_IN_STATIC": "trip_id absent from the static GTFS (E003)",
    "STOP_NOT_IN_STATIC": "stop_id absent from stops.txt (E011)",
}


def uic_from_stop_id(stop_id: str | None) -> str | None:
    m = _UIC.search(stop_id) if stop_id else None
    return m.group(1) if m else None


def _opt(msg, field: str) -> Any:
    return getattr(msg, field) if msg.HasField(field) else None


def trip_update_rows(msg: FeedMessage, feed_ts: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ent in msg.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        trip = tu.trip
        base: dict[str, Any] = {
            "feed_ts": feed_ts,
            "start_date": trip.start_date or None,
            "trip_id": trip.trip_id or None,
            "route_id": trip.route_id or None,
            "trip_schedule_relationship": enum_name(TripRel, trip.schedule_relationship),
        }
        trip_flags = [] if trip.trip_id else ["MISSING_TRIP_ID"]

        if not tu.stop_time_update:
            rows.append(
                {
                    **base,
                    **_empty_stop(),
                    "defect_flags": [*trip_flags, "NO_STOP_TIME_UPDATE"],
                }
            )
            continue

        seen_seq: set[int] = set()
        seen_stop: set[str] = set()
        prev_seq: int | None = None
        for stu in tu.stop_time_update:
            flags = list(trip_flags)
            stop_id = stu.stop_id or None
            seq = _opt(stu, "stop_sequence")
            if seq is not None:
                if prev_seq is not None and seq < prev_seq:
                    flags.append("UNSORTED_STOP_TIME_UPDATE")
                if seq in seen_seq:
                    flags.append("DUP_STOP_SEQUENCE")
                seen_seq.add(seq)
                prev_seq = seq
            if stop_id is None:
                flags.append("NO_STOP_ID")
            else:
                if stop_id in seen_stop:
                    flags.append("DUP_STOP_ID")
                seen_stop.add(stop_id)
            uic = uic_from_stop_id(stop_id)
            if stop_id is not None and uic is None:
                flags.append("NO_UIC")
            rows.append(
                {
                    **base,
                    "stop_id": stop_id,
                    "uic": uic,
                    "stop_sequence": seq,
                    "arrival_delay": _opt(stu.arrival, "delay"),
                    "departure_delay": _opt(stu.departure, "delay"),
                    "arrival_time": _opt(stu.arrival, "time"),
                    "departure_time": _opt(stu.departure, "time"),
                    "stop_schedule_relationship": enum_name(StopRel, stu.schedule_relationship),
                    "defect_flags": flags,
                }
            )
    return rows


def _empty_stop() -> dict[str, Any]:
    return {
        "stop_id": None,
        "uic": None,
        "stop_sequence": None,
        "arrival_delay": None,
        "departure_delay": None,
        "arrival_time": None,
        "departure_time": None,
        "stop_schedule_relationship": None,
    }
