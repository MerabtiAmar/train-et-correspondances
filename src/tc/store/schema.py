"""Arrow schemas, reduction keys and row <-> table conversion for each dataset kind."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyarrow as pa

TS = pa.timestamp("s", tz="UTC")

_AGG = [
    pa.field("first_seen_ts", TS),
    pa.field("last_seen_ts", TS),
    pa.field("n_updates", pa.int32()),
]

TU_SCHEMA = pa.schema(
    [
        pa.field("feed_ts", TS),
        pa.field("start_date", pa.string()),
        pa.field("trip_id", pa.string()),
        pa.field("route_id", pa.string()),
        pa.field("trip_schedule_relationship", pa.string()),
        pa.field("stop_id", pa.string()),
        pa.field("uic", pa.string()),
        pa.field("stop_sequence", pa.int32()),
        pa.field("arrival_delay", pa.int32()),
        pa.field("departure_delay", pa.int32()),
        pa.field("arrival_time", TS),
        pa.field("departure_time", TS),
        pa.field("stop_schedule_relationship", pa.string()),
        pa.field("defect_flags", pa.list_(pa.string())),
        *_AGG,
    ]
)

ALERTS_SCHEMA = pa.schema(
    [
        pa.field("feed_ts", TS),
        pa.field("alert_id", pa.string()),
        pa.field("cause", pa.string()),
        pa.field("effect", pa.string()),
        pa.field(
            "active_periods",
            pa.list_(pa.struct([pa.field("start", TS), pa.field("end", TS)])),
        ),
        pa.field(
            "informed_entities",
            pa.list_(
                pa.struct(
                    [
                        pa.field("trip_id", pa.string()),
                        pa.field("route_id", pa.string()),
                        pa.field("stop_id", pa.string()),
                    ]
                )
            ),
        ),
        pa.field("header_text", pa.string()),
        pa.field("description_text", pa.string()),
        *_AGG,
    ]
)

TU_KEY = ("start_date", "trip_id", "stop_id", "stop_sequence")
ALERTS_KEY = ("alert_id",)

# kind (= top-level folder under data/) -> (schema, reduction key)
KINDS: dict[str, tuple[pa.Schema, tuple[str, ...]]] = {
    "reduced": (TU_SCHEMA, TU_KEY),
    "alerts": (ALERTS_SCHEMA, ALERTS_KEY),
}


def rows_to_table(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def _epoch(value: Any) -> Any:
    return int(value.timestamp()) if isinstance(value, datetime) else value


def epoch_seconds(column: pa.ChunkedArray) -> pa.ChunkedArray:
    """Timestamp column to int64 epoch seconds, whatever its unit.

    Parquet cannot store second-resolution timestamps: a file written from timestamp[s] is
    read back as timestamp[ms]. Comparing raw int64 values across that boundary is a bug.
    """
    return column.cast(TS, safe=False).cast(pa.int64())


def table_to_rows(table: pa.Table) -> list[dict[str, Any]]:
    """Inverse of rows_to_table: timestamps come back as epoch seconds (what parsers emit)."""
    for i, field in enumerate(table.schema):
        if pa.types.is_timestamp(field.type):
            table = table.set_column(i, field.name, epoch_seconds(table.column(i)))
    rows = table.to_pylist()
    if "active_periods" in table.column_names:  # nested timestamps
        for row in rows:
            for period in row["active_periods"] or []:
                period["start"] = _epoch(period["start"])
                period["end"] = _epoch(period["end"])
    return rows
