---
paths:
  - "src/tc/ingest/**"
  - "src/tc/store/**"
  - "tests/**"
  - "scripts/**"
---

# SNCF feeds: verified facts (November 2026, transport.data.gouv.fr)

## URLs
- Trip Updates (GTFS-RT): `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates`. The former fallback `.../sncf-all-gtfs-rt-trip-updates` answers HTTP 404 (checked 2026-11-17), so `TRIP_UPDATES_URL_FALLBACK` is empty by default; the fallback mechanism stays for a future second source.
- Observed on the first night (2026-11-17, 00:44 Paris): one single empty cycle of the primary feed. Cause not verified.
- Service Alerts (GTFS-RT): `https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts`
- Static GTFS (TGV, Intercités, TER), about 4-5 MB zip: `https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip`
- Spare real-time source, not used yet: `https://proxy.transport.data.gouv.fr/resource/sncf-siri-lite-estimated-timetable`
- All URLs configurable via env vars.

## Behaviour
- Trip Updates refresh every 2 minutes and only cover trains running in the next 60 minutes. A train's final delay at a stop = the last estimate seen before it passes that stop. Poll every 120 s; one request per feed per cycle; User-Agent with project name and contact e-mail from env.
- One snapshot weighs about 1 MB: reduce at source, never store every snapshot.
- The feed has been empty for days at a time (2025 incidents). An empty or stale feed is a normal state: log it, count it, keep polling.

## Known data defects (national validator)
- trip_ids missing from the static GTFS (E003). Real ids look like `OCESN877703F4849586:2025-07-10T23:16:05Z`: they embed a timestamp, and apparently the commercial train number after `OCESN`. Verify on real data before relying on that.
- stop_ids missing from stops.txt (E011). Stop ids look like `StopPoint:OCETGV INOUI-87391003`: the last 8 digits are the UIC station code. Use the UIC code as the stable station key.
- stop_time_updates not sorted (E002), duplicate stop_sequence (E036), same stop twice in a trip (E009).
- Rule: never crash on these. Keep the row, flag it, count it in the daily coverage report.
- Service Alerts use the commercial train number as the common identifier across feeds (documented by SNCF).

## Storage conventions
- Timestamps stored in UTC. Keep `start_date` (service date) as given by the feed.
- Reduced key: (start_date, trip_id, stop_id, stop_sequence); latest feed timestamp wins.
- Partitions: `data/reduced/date=YYYY-MM-DD/`. Writes are atomic (temp file + rename).
