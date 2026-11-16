# 0005 — Memory budget (container capped at 768 MB)

Status: accepted (session 1, measured on 2026-11-16)

## Measurements
Peak working set of one process on the Windows dev machine (Linux numbers will differ somewhat, check
`docker stats` on the VM). The synthetic day is deliberately pessimistic: 24 busy hours of 48 000 keys
each (1.15 M hourly rows, 600 000 distinct keys). The real 10-minute run had 16 500 keys.

| Step | Peak |
|---|---|
| Real run: static GTFS download + streaming conversion (529 k stop_times rows) + 1 poll | 232 MB |
| One busy hour of in-memory state (48 k keys, 30 snapshots) + flush | 148 MB |
| Daily compaction, first version (Python dict over all keys) | **1 221 MB: would be OOM-killed every night** |
| Compaction in Arrow (hash group-by on key + aggregate columns, rows copied file by file) | 495 MB |
| + text keys read dictionary-encoded, only aggregates kept | 391 MB |
| + Arrow limited to 1 thread (`pa.set_cpu_count(1)`, same duration: 1.7 s) | **335 MB** |

## Decision
Compaction stays Arrow-only and never builds Python objects per row; Arrow runs single-threaded (the
container has 0.5 CPU anyway). The in-memory hourly state stays Python dicts: it is bounded by one hour.

## Revisit if
`docker stats` on the VM shows more than ~500 MB around 03:00 Paris (compaction time), or the feed grows
well beyond the pessimistic figures above. Next step would be compacting in key partitions
(for example by `stop_sequence % 4`), which divides the peak further.
