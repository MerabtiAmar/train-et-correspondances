"""Builders for synthetic GTFS-RT snapshots (used for defects that a real fixture may lack)."""

from __future__ import annotations

from google.transit import gtfs_realtime_pb2 as pb


def stop(stop_id=None, seq=None, a_delay=None, d_delay=None, a_time=None, d_time=None, rel=None):
    return {
        "stop_id": stop_id,
        "seq": seq,
        "a_delay": a_delay,
        "d_delay": d_delay,
        "a_time": a_time,
        "d_time": d_time,
        "rel": rel,
    }


def trip_entity(
    msg: pb.FeedMessage,
    trip_id="TRIP1",
    stops=(),
    route_id="ROUTE1",
    start_date="20261116",
    rel=None,
):
    ent = msg.entity.add()
    ent.id = f"e{len(msg.entity)}"
    tu = ent.trip_update
    if trip_id is not None:
        tu.trip.trip_id = trip_id
    tu.trip.route_id = route_id
    tu.trip.start_date = start_date
    if rel is not None:
        tu.trip.schedule_relationship = rel
    for s in stops:
        stu = tu.stop_time_update.add()
        if s["stop_id"] is not None:
            stu.stop_id = s["stop_id"]
        if s["seq"] is not None:
            stu.stop_sequence = s["seq"]
        if s["a_delay"] is not None:
            stu.arrival.delay = s["a_delay"]
        if s["d_delay"] is not None:
            stu.departure.delay = s["d_delay"]
        if s["a_time"] is not None:
            stu.arrival.time = s["a_time"]
        if s["d_time"] is not None:
            stu.departure.time = s["d_time"]
        if s["rel"] is not None:
            stu.schedule_relationship = s["rel"]
    return ent


def feed(ts: int = 1_800_000_000) -> pb.FeedMessage:
    msg = pb.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.incrementality = pb.FeedHeader.FULL_DATASET
    if ts:
        msg.header.timestamp = ts
    return msg


def alert_entity(msg: pb.FeedMessage, alert_id="A1", fr="Travaux", en="Works", trips=(), stops=()):
    ent = msg.entity.add()
    ent.id = alert_id
    a = ent.alert
    a.cause = pb.Alert.CONSTRUCTION
    a.effect = pb.Alert.DETOUR
    p = a.active_period.add()
    p.start = 1_800_000_000
    if en:
        t = a.header_text.translation.add()
        t.text, t.language = en, "en"
    if fr:
        t = a.header_text.translation.add()
        t.text, t.language = fr, "fr"
    d = a.description_text.translation.add()
    d.text, d.language = "Description sans langue française", "de"
    for trip_id in trips:
        a.informed_entity.add().trip.trip_id = trip_id
    for stop_id in stops:
        a.informed_entity.add().stop_id = stop_id
    return ent
