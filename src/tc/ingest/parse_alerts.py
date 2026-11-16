"""Service Alerts: one output row per alert (periods and informed entities kept as nested lists)."""

from __future__ import annotations

from typing import Any

from google.transit import gtfs_realtime_pb2

from tc.ingest.decode import FeedMessage, enum_name

Cause = gtfs_realtime_pb2.Alert.Cause
Effect = gtfs_realtime_pb2.Alert.Effect


def french_text(translated) -> str | None:
    """French translation if present, else the first one, else None."""
    first = None
    for tr in translated.translation:
        if tr.language.lower().startswith("fr"):
            return tr.text or None
        if first is None:
            first = tr.text or None
    return first


def alert_rows(msg: FeedMessage, feed_ts: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ent in msg.entity:
        if not ent.HasField("alert"):
            continue
        alert = ent.alert
        rows.append(
            {
                "feed_ts": feed_ts,
                "alert_id": ent.id or None,
                "cause": enum_name(Cause, alert.cause),
                "effect": enum_name(Effect, alert.effect),
                "active_periods": [
                    {
                        "start": p.start if p.HasField("start") else None,
                        "end": p.end if p.HasField("end") else None,
                    }
                    for p in alert.active_period
                ],
                "informed_entities": [
                    {
                        "trip_id": (e.trip.trip_id or None) if e.HasField("trip") else None,
                        "route_id": e.route_id
                        or (e.trip.route_id if e.HasField("trip") else "")
                        or None,
                        "stop_id": e.stop_id or None,
                    }
                    for e in alert.informed_entity
                ],
                "header_text": french_text(alert.header_text),
                "description_text": french_text(alert.description_text),
            }
        )
    return rows
