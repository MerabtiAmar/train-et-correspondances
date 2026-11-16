# 0001 — Reduction key, hourly state, duplicate keys

Status: accepted (session 1)

## Decision
- Key = `(start_date, trip_id, stop_id, stop_sequence)`, as in `.claude/rules/gtfs-rt.md`. The row with the
  highest feed timestamp wins; ties go to the row received last. A late or replayed snapshot never
  overwrites newer data, it only extends `first_seen_ts` and `n_updates`.
- The in-memory state lives **per UTC hour** and is dropped at the hour change (its file is written first).
  The same (trip, stop) can therefore appear in two consecutive hourly files. Daily compaction merges them:
  latest `feed_ts` wins, `first_seen_ts` = min, `last_seen_ts` = max, `n_updates` = sum.
- On restart, the current hour's file is reloaded into the state before new data is folded in, so the
  10-minute rewrite never replaces earlier data of the same hour with a shorter file.
- Same key twice in one snapshot (defects E036 / E009): the last occurrence is kept, flagged `DUP_KEY`,
  counted in the coverage report, and counts as one update (not two).
- A whole-trip update with no `stop_time_update` (typically a cancellation) is kept as one row with null
  stop fields and the flag `NO_STOP_TIME_UPDATE`: cancellations are the label we care about most.

## Why
Memory must stay far below the 768 MB cap on a VM shared with another app. A full-day state would hold every
key of the day; an hourly state holds about one hour of trains, and a train's estimates only change while it
is in the 60-minute window. The cost (a key split across two hours) is cheap to undo at compaction.

## Revisit if
The feed starts covering more than 60 minutes ahead, or hourly files get large enough to make the
10-minute rewrite slow.
