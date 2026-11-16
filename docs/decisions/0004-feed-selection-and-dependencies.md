# 0004 — Trip Updates source selection, and the one extra dependency

Status: accepted (session 1)

## Feed selection
Each cycle asks the primary Trip Updates URL first. The fallback URL is asked **only** if the primary gave no
usable snapshot (error, undecodable, empty, or older than `STALE_AFTER_MIN` = 10 min). So there is at most one
request per URL per cycle, and the collector returns to the primary by itself when it recovers. If the only
non-empty snapshot is stale it is still archived (and counted as stale): late data is still data, and the
reducer orders by feed timestamp. A snapshot whose header timestamp equals the previous one is skipped
(counted), after the staleness check, so a frozen feed still triggers the fallback.
Alerts: one URL; an empty alerts feed is a legitimate state (counted, not an error).

## Dependency: tzdata
`zoneinfo.ZoneInfo("Europe/Paris")` needs the IANA database. Windows (dev machine) has none in a uv venv,
and slim images may not either. `tzdata` is pure data (~0.5 MB). Approved by the owner on 2026-11-16; it is
the only dependency beyond the Stack in CLAUDE.md. `uv_build` (uv's own backend) is a build requirement only.
