# Progress

## Status
Phase 0: collector **deployed** on 2026-11-16 on its own Always Free VM `tc-collector`
(Ubuntu 24.04, VM.Standard.E2.1.Micro, 952 MB RAM + 2 GB swap, Docker 29), reached with `ssh tc-vm`.
The family VM `siwel-turn` (coturn, 1 GB) is untouched and off-limits; Claude's key was removed from it.
Container healthy, caps applied (0.5 CPU, 768 MB, 128 pids), no published port, VM ports unchanged.
First cycles on the VM: feeds fetched, raw samples written, static GTFS converted, 194 MB used.
Upload enabled (`"dry_run": false`): HF token copied by the owner, write right checked (`repo.write`
on the owner's namespace), dataset card published to `AmarMerabti/train-correspondances-gtfs-rt`.
Local `.env.vm` deleted afterwards (OneDrive-synced folder); the token now lives only on the VM.
`uv run pytest -q`: 99 passed, `ruff check` clean. Memory budget: ADR 0005 (compaction 335 MB peak).

Checked 2026-11-17 18:00 Paris: first nightly upload OK (day 2026-11-16, 17 files, 10.4 MB; 108
snapshots since 20:23, no gap/error; 13 362 rows, 1 632 trains, UIC 100 %). Memory peak 353 MB over the
night (compaction), 0 restart, 0 OOM. Today so far: 538/538 snapshots, no gap. Dead fallback URL
(HTTP 404) disabled; VM port check now ignores loopback listeners (containerd port changes on restart).
Known defect, expected: TRIP_NOT_IN_STATIC ~310 rows per snapshot (E003).

Tooling: `scripts/oci_create_vm.sh` (Cloud Shell: creates the VM, retries Oracle 429, falls back to
Micro, never creates two), `scripts/vm.sh prepare|install|status|configure|update|stop|uninstall`.

## Next
1. Optional hardening: restrict the HF token to this single dataset (today it can write to every repo
   of the owner's namespace); then `bash scripts/vm.sh configure` on the VM.
2. Phase 0 interviews / waiting list (docs/PLAN.md); Phase 1 labelling needs a few weeks of data.

## Open questions
- Dry-run at `MAX_LOCAL_MB`: raw samples pause, reduced data keeps being written (ADR 0003). Kept as is.
- `siwel-turn` has an ephemeral public IP (owner's own note): unrelated to the collector.

## Decisions
ADRs 0001-0005 in `docs/decisions/`. Owner autonomy rule and VM layout in CLAUDE.md.
