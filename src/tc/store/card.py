"""Hugging Face dataset card (README.md of the dataset repo), regenerated at each upload."""

from __future__ import annotations

CARD = """\
---
license: odbl
language:
- fr
pretty_name: SNCF GTFS-RT archive (reduced trip updates and service alerts)
tags:
- gtfs-realtime
- sncf
- railway
- punctuality
- transport
configs:
- config_name: trip_updates
  data_files: "reduced/date=*/day.parquet"
- config_name: alerts
  data_files: "alerts/date=*/day.parquet"
---

# SNCF GTFS-RT archive

Données SNCF Voyageurs, licence ODbL.

Daily archive of the SNCF real-time feeds (Trip Updates and Service Alerts), polled every 2 minutes
and **reduced at source**. The real-time feed only covers the next 60 minutes, so this archive is the
only way to get the observed delay of a train after the fact. Independent project, not affiliated
with SNCF. Published openly on purpose: this derived database is shared under the same ODbL licence
(share-alike).

## Layout

| Path | Content |
|---|---|
| `reduced/date=YYYY-MM-DD/day.parquet` | Trip Updates, one row per (service date, trip, stop, stop sequence): the **latest estimate seen** |
| `alerts/date=YYYY-MM-DD/day.parquet` | Service Alerts, one row per alert id (latest version) |
| `raw/date=YYYY-MM-DD/*.pb.gz` | Hourly raw protobuf snapshots, for reprocessing |
| `static/date=YYYY-MM-DD/` | Static GTFS (zip + Parquet), only on days it changed |
| `coverage/date=YYYY-MM-DD/coverage.json` | Collector report: polls done, gaps, empty/stale feeds, defects |

`date` is the Europe/Paris calendar day of collection. All timestamps are UTC.

## Trip Updates schema (`reduced`)

`feed_ts`, `start_date` (service date as given by the feed, `YYYYMMDD`), `trip_id`, `route_id`,
`trip_schedule_relationship`, `stop_id`, `uic` (8-digit station code taken from the end of
`stop_id`), `stop_sequence`, `arrival_delay` / `departure_delay` (seconds), `arrival_time` /
`departure_time` (UTC), `stop_schedule_relationship`, `defect_flags` (list of known feed defects),
`first_seen_ts`, `last_seen_ts`, `n_updates` (how many snapshots carried this key).

The final delay of a train at a stop is the last estimate seen before it passed that stop: that is
exactly the value kept by the reduction.

## Alerts schema (`alerts`)

`feed_ts`, `alert_id`, `cause`, `effect`, `active_periods` (list of `{start, end}`),
`informed_entities` (list of `{trip_id, route_id, stop_id}`), `header_text`, `description_text`
(French when available), `first_seen_ts`, `last_seen_ts`, `n_updates`.

## Load

```python
from datasets import load_dataset
trips = load_dataset("{repo_id}", "trip_updates", split="train")
```

```python
import duckdb
duckdb.sql(\"\"\"
    SELECT start_date, count(*) AS rows, avg(arrival_delay) / 60 AS mean_delay_min
    FROM 'hf://datasets/{repo_id}/reduced/date=*/day.parquet'
    GROUP BY 1 ORDER BY 1
\"\"\")
```

## Caveats

- The feed has had multi-day outages: check `coverage/` before computing statistics.
- Known upstream defects are kept and flagged in `defect_flags`, never silently repaired
  (trip ids absent from the static GTFS, unsorted or duplicated stop updates...).
"""


def render_card(repo_id: str) -> str:
    return CARD.replace("{repo_id}", repo_id)
