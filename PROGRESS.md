# Progress

## Status
Phase 0: collector **in production** since 2026-11-16 on its own Always Free VM `tc-collector`
(Ubuntu 24.04, E2.1.Micro, 952 MB + 2 GB swap, Docker), `ssh tc-vm`. Family VM `siwel-turn` untouched,
off-limits, no Claude key on it. Caps applied (0.5 CPU, 768 MB, 128 pids), no published port.
Upload on: HF token on the VM only (`repo.write` on the owner's namespace), dataset card published.
Check 2026-11-17 18:00 Paris: first nightly upload OK (day 16: 17 files, 10.4 MB, 108 snapshots,
no gap or error, 13 362 rows, 1 632 trains, UIC 100 %). Night memory peak 353 MB, 0 restart, 0 OOM.
Today: 538/538 snapshots, no gap. Dead fallback URL (404) disabled; port check ignores loopback.
Expected defect: TRIP_NOT_IN_STATIC ~310 rows per snapshot (E003).
`uv run pytest -q`: 99 passed; ruff clean. Tooling: `scripts/oci_create_vm.sh` (Cloud Shell),
`scripts/vm.sh prepare|install|status|configure|update|stop|uninstall`.

## Next
1. Optional hardening: HF token limited to this one dataset, then `bash scripts/vm.sh configure`.
2. Phase 0 interviews / waiting list (docs/PLAN.md); Phase 1 labelling needs a few weeks of data.

## Open questions
- Dry-run at `MAX_LOCAL_MB`: raw samples pause, reduced data keeps being written (ADR 0003). Kept.
- Does the Trip Updates feed list every running train or only disrupted ones? Matters for Phase 1.

## Decisions
ADRs 0001-0005 in `docs/decisions/`. Owner autonomy rule and VM layout in CLAUDE.md.
