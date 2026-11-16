# Data produced by the collector

Source: SNCF GTFS-RT and static GTFS (transport.data.gouv.fr). Données SNCF Voyageurs, licence ODbL.
Everything below is published daily to the public Hugging Face dataset (`HF_REPO`).

## Layout

```
data/
  reduced/date=YYYY-MM-DD/<YYYYMMDDTHHZ>.parquet   hourly, rewritten every 10 min (today only)
  reduced/date=YYYY-MM-DD/day.parquet               after compaction of a finished day
  alerts/date=YYYY-MM-DD/...                        same pattern, for Service Alerts
  raw/date=YYYY-MM-DD/<feed>_<YYYYMMDDTHHMMSSZ>.pb.gz   one gzipped protobuf per feed per hour
  static/date=YYYY-MM-DD/{gtfs.zip,trips,stop_times,stops,routes,calendar_dates}.*   only when changed
  coverage/date=YYYY-MM-DD/coverage.json            daily report (provisional for today)
  .state/                                           heartbeat, uploaded manifest, stats, static sha256
```

- `date=` is the **Europe/Paris collection date** (poll time). Hour labels are **UTC**. See ADR 0002.
- All timestamps are UTC. Parquet stores them with millisecond resolution (values are whole seconds).
- The service day of a train is `start_date`, as given by the feed: night trains run after midnight.
- Load everything with a glob, e.g. DuckDB `read_parquet('reduced/date=*/day.parquet')`.

## `reduced` — Trip Updates (one row per key, latest estimate)

Key: **(`start_date`, `trip_id`, `stop_id`, `stop_sequence`)**. Latest `feed_ts` wins (ADR 0001). A train's
final delay at a stop is the last estimate seen before it passed: exactly what is kept.

| Column | Type | Meaning |
|---|---|---|
| `feed_ts` | timestamp | Header timestamp of the snapshot this row comes from |
| `start_date` | string | Service date `YYYYMMDD`, as given |
| `trip_id` | string | e.g. `OCESN877703F4849586:2025-07-10T23:16:05Z` |
| `route_id` | string | |
| `trip_schedule_relationship` | string | `SCHEDULED`, `ADDED`, `CANCELED`, ... |
| `stop_id` | string | e.g. `StopPoint:OCETGV INOUI-87391003` (null for whole-trip updates) |
| `uic` | string | 8 digits taken from the end of `stop_id`; the stable station key (null if absent) |
| `stop_sequence` | int32 | |
| `arrival_delay`, `departure_delay` | int32 | seconds, may be negative |
| `arrival_time`, `departure_time` | timestamp | absolute predicted time |
| `stop_schedule_relationship` | string | `SCHEDULED`, `SKIPPED`, `NO_DATA`, ... |
| `defect_flags` | list&lt;string&gt; | known feed defects on this row, see below |
| `first_seen_ts`, `last_seen_ts` | timestamp | first and last snapshot that carried this key |
| `n_updates` | int32 | number of snapshots that carried this key |

Null delays/times are normal (a stop with only an arrival, or with no estimate).

## `alerts` — Service Alerts (one row per alert id, latest version)

Key: **`alert_id`**. Columns: `feed_ts`, `alert_id`, `cause`, `effect`,
`active_periods` (list of `{start, end}` timestamps, either may be null),
`informed_entities` (list of `{trip_id, route_id, stop_id}`), `header_text`, `description_text`
(French translation, else the first available, else null), plus `first_seen_ts`, `last_seen_ts`,
`n_updates`. Alerts identify trains by the commercial train number (SNCF documentation).

## Defect flags (`defect_flags`, counted per day in `coverage.json`)

| Flag | Meaning |
|---|---|
| `MISSING_TRIP_ID` | trip descriptor without `trip_id` |
| `NO_STOP_TIME_UPDATE` | trip update without any stop update (e.g. whole-trip cancellation) |
| `NO_STOP_ID` / `NO_UIC` | stop update without `stop_id` / `stop_id` not ending with 8 digits |
| `UNSORTED_STOP_TIME_UPDATE` | `stop_sequence` goes backwards in a trip (validator E002) |
| `DUP_STOP_SEQUENCE` | `stop_sequence` repeated in a trip (E036) |
| `DUP_STOP_ID` | same `stop_id` twice in a trip (E009) |
| `DUP_KEY` | same reduction key twice in one snapshot (added by the reducer) |
| `TRIP_NOT_IN_STATIC` | `trip_id` absent from the latest static GTFS (E003) |
| `STOP_NOT_IN_STATIC` | `stop_id` absent from `stops.txt` (E011) |

Defects are never repaired or dropped. Counts in the coverage report are *occurrences across snapshots*
(a defective trip seen in 20 snapshots counts 20 times); the flag on a reduced row describes its latest
snapshot only. `TRIP/STOP_NOT_IN_STATIC` are only set once a static GTFS has been downloaded.

## `static` — static GTFS

`gtfs.zip` (as downloaded, kept only when its sha256 changed) and one Parquet per table: `trips`,
`stop_times`, `stops`, `routes`, `calendar_dates`. All columns are text, except `stop_sequence`,
`route_type`, `exception_type`, `direction_id`, `location_type` (int32). Times such as `25:10:00`
(after midnight) stay text.

## `raw` — raw samples

Gzipped protobuf `FeedMessage`, one per feed (`trip_updates`, `alerts`) per hour (`RAW_SAMPLE_EVERY_MIN`),
to re-run a better parser later. Decode with `gzip.decompress` then `FeedMessage.ParseFromString`.

## `coverage.json`

`day`, `complete` (false while the day is running), `polls_expected` / `polls_done` /
`polls_skipped_overrun`, then for `trip_updates` and `alerts`: `distinct_snapshots`, `fetch_ok`,
`fetch_errors`, `not_modified`, `unchanged_skipped`, `empty_feeds`, `stale_feeds`, `parse_errors`,
`sources` (URL → snapshots used), and for trip updates `gaps_over_6_min` (list of `from`, `to`, `minutes`,
including the edges of the day). Also `defects` (flag → count), `rows_written` (per dataset) and
`bytes_per_file`. Compute any statistic only after checking the gaps of the days involved.

## State files (`data/.state/`, never uploaded)

`heartbeat.json` (read by `tc.health`), `uploaded.json` (path → size of every file confirmed on the Hub;
retention only deletes files listed here), `stats-<date>.json` (counters of a running day, survive a
restart), `static.json` (sha256 and last check date of the static GTFS).
