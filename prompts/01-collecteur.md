<task>
Session 1: build the GTFS-RT collector of Phase 0. The repo only holds the starter kit; there is no code yet.
</task>

<why>
The real-time feed only covers the next 60 minutes, so every day not archived is lost forever. This collector is the project's only irreplaceable asset. It runs 24/7 on a free Oracle VM shared with my family's video-call app, so it must be small, quiet and impossible to harm that app.
</why>

<context>
CLAUDE.md is loaded. Feed URLs, data defects and storage conventions are in .claude/rules/gtfs-rt.md, which loads when you touch src/tc/ingest, src/tc/store, tests or scripts. Do not look for them elsewhere.
</context>

<requirements>
1. Config: pydantic-settings, every value from env with a default, plus `.env.example` (feed URLs, POLL_SECONDS=120, HF_REPO, HF_TOKEN, CONTACT_EMAIL, MAX_LOCAL_MB=2048, RAW_SAMPLE_EVERY_MIN=60, RUN_FOR_MINUTES=0 meaning forever).
2. Fetch: httpx, 20 s timeout, 3 retries with exponential backoff and jitter, conditional GET when the server supports it. Skip a snapshot whose feed header timestamp did not change.
3. Parse Trip Updates into rows: feed_ts, start_date, trip_id, route_id, trip_schedule_relationship, stop_id, uic (8 digits), stop_sequence, arrival_delay, departure_delay, arrival_time, departure_time, stop_schedule_relationship, defect_flags. Parse Alerts into rows: feed_ts, alert_id, cause, effect, active periods, informed entities (trip_id, route_id, stop_id), header and description text in French.
4. Reduce: in-memory state keeping the latest row per key, plus first_seen_ts, last_seen_ts and n_updates. Every 10 minutes, atomically rewrite the current hour's Parquet file from that state, so a restart loses at most 10 minutes. Daily compaction merges the hourly files of the previous day, latest wins.
5. Raw samples: one gzipped protobuf snapshot per RAW_SAMPLE_EVERY_MIN, for reprocessing.
6. Static GTFS: download once a day, keep it only if its sha256 changed, convert trips, stop_times, stops, routes, calendar_dates to Parquet.
7. Upload: after 03:00 Europe/Paris, push the previous day (reduced, alerts, static if changed, raw samples, coverage report) to the HF dataset with huggingface_hub. Create or update the dataset card: ODbL attribution, schema, how to load. Delete the local copy only after the upload is confirmed. Without HF_TOKEN, run in dry-run and keep files.
8. Retention: if data/ exceeds MAX_LOCAL_MB, delete oldest raw samples first, then oldest already-uploaded files. Never delete data that has not been uploaded; log an error instead.
9. Coverage report (daily JSON): polls expected and done, distinct snapshots, gaps over 6 minutes, empty or stale feeds, parse errors, defect counts by type, rows written, bytes per file.
10. Ops: one process, a simple loop aligned on a monotonic clock (no asyncio, no scheduler framework, no database). JSON logs to stdout. Heartbeat file; `python -m tc.health` exits 1 if the heartbeat is older than 10 minutes. Clean shutdown on SIGTERM (flush state).
11. Docker: python:3.12-slim, non-root user, `docker/compose.yaml` with `cpus: "0.5"`, `mem_limit: 768m`, `restart: unless-stopped`, json-file logging (max-size 10m, max-file 3), healthcheck on `tc.health`, volume `./data`, env_file `.env`, and no `ports:` section at all.
12. Tests, offline only: parser on a real fixture, reducer (latest wins, out-of-order snapshots, defects kept and flagged), atomic hourly write and daily compaction, retention order, uploader with a fake client. Write `scripts/record_fixture.py` that downloads one snapshot per feed and trims it to under 200 KB; run it once, commit the fixtures.
13. Docs: `docs/DATA.md` (schemas and keys). `docs/DEPLOY.md` in French, step by step for me on the Oracle VM: prerequisites check, clone, `.env`, build and start, check logs and health, stop in one command, uninstall, update. Include a "sécurité de l'application existante" section: how to confirm the caps are applied (`docker stats`), that no port is open (`ss -tlnp` before and after), and what to do if memory is tight.
</requirements>

<constraints>
- Nothing outside this folder. No ssh, no remote resource creation, no paid service.
- Dependencies: the Stack in CLAUDE.md only. Ask before adding anything else.
- Prefer boring, small code over clever abstractions. One module per responsibility.
</constraints>

<process>
1. Start with a plan of at most 15 lines: files to create, key decisions, risks. Wait for my OK.
2. Then implement in this order, running the matching tests after each step: config and logging → fetch → fixture recording → parse → reduce → hourly write and compaction → static GTFS → upload → retention → coverage → main loop and health → Docker → docs.
3. Record one ADR in docs/decisions/ for any choice I might question later (for example the reduction key).
</process>

<done_when>
- `uv run pytest -q` passes and `uv run ruff check .` is clean (show the last lines of each).
- A local run with `RUN_FOR_MINUTES=10` produces a Parquet file and a coverage report; report their sizes and the measured size of one raw snapshot.
- `docker compose -f docker/compose.yaml config` validates. If Docker is installed, the image builds; if not, say so.
- PROGRESS.md updated. Final message in French, at most 10 lines: what works, what I must do (create the HF dataset and token, fill `.env`, follow DEPLOY.md), open questions.
</done_when>
