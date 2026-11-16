# Progress

## Status
Phase 0, session 1 done: GTFS-RT collector built, run locally on real feeds, memory measured.
`uv run pytest -q`: 94 tests pass, `uv run ruff check .` clean.
Local 10-min run (dry-run): 5 polls, no warning/error, primary Trip Updates URL used.
- reduced hourly Parquet 167 KB (16 552 rows), alerts 23 KB (136 rows), coverage.json 2 KB
- one raw snapshot: Trip Updates 215-228 KB gzipped (~1.1 MB protobuf), Alerts 50-53 KB gzipped
- static GTFS: 5.6 MB zip, 529 k stop_times rows converted in streaming
- restart on real data resumes the current hour file (16 552 -> 16 958 rows, counters continue)
Memory (ADR 0005): first daily compaction peaked at 1.2 GB on a pessimistic day (OOM under the 768 MB cap);
rewritten in Arrow, now 335 MB. Real run with static conversion: 232 MB. Busy hour state: 148 MB.

Not verified: Docker is not installed on the dev machine, so `docker compose config` and the image build
were not run (compose.yaml is checked by tests: YAML parse, no `ports`, caps, healthcheck). Memory was
measured on Windows: confirm with `docker stats` on the VM, especially around 03:00 Paris.

## Next
1. Owner: create the public HF dataset + write token limited to it, fill `.env` on the VM, follow
   `docs/DEPLOY.md` (repo URL already filled in).
2. After 24 h on the VM: read `coverage.json`, check the first `day uploaded` log, the dataset card, and
   `docker stats` at compaction time.
3. Then Phase 0 interviews / waiting list (docs/PLAN.md); Phase 1 (labelling) needs a few weeks of data.

## Open questions
- VM architecture (arm64 or x86_64)? Not blocking: the image is built on the VM.
- Dry-run keeps everything; at `MAX_LOCAL_MB` raw samples pause, reduced data keeps being written (ADR 0003).
  Is that the wanted behaviour, or should the collector stop instead?

## Decisions
ADRs 0001-0005 in `docs/decisions/` (hourly state and key, partition day, upload/retention safety, feed
selection and the `tzdata` dependency approved on 2026-11-16, memory budget).
