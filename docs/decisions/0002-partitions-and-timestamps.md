# 0002 — Partition day, hour labels, timestamp units

Status: accepted (session 1)

## Decision
- Folder `date=YYYY-MM-DD` is the **Europe/Paris** calendar date of the collection time (poll time, not
  the feed timestamp). All timestamps inside files are UTC.
- Hourly files are named by their **UTC** hour: `20261116T10Z.parquet`. A bare `HH` would repeat on the
  25-hour DST change day.
- Parquet cannot store second-resolution timestamps: columns declared `timestamp[s]` come back as
  `timestamp[ms]`. Code that compares raw integers across a read must go through
  `tc.store.schema.epoch_seconds` (a test caught exactly this bug on restart).

## Why
"Upload the previous day after 03:00 Paris" only makes sense if a day is a Paris day. Night trains of
service date D run after midnight, so `start_date` (kept as given by the feed) is the field to use for the
service day; `date=` is a collection partition. Paris offsets are whole hours, so every UTC hour belongs to
exactly one Paris date.

## Revisit if
Analysis shows the service-day / collection-day mismatch is painful: add a derived column in Phase 1
rather than changing the layout.
