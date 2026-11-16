# Train & Correspondances

Predict, before booking, the probability that a French train is late or cancelled and that a rail connection holds (P(arrival train 1 + transfer time <= departure train 2)), as calibrated probabilities. Data: SNCF open data (GTFS static + GTFS-RT), licence ODbL.

- Status and next step: `PROGRESS.md`. Read it at session start; it is the only handoff between sessions.
- Plan: `docs/PLAN.md` (condensed). `docs/plan-complet.pdf` is the full 11-page French original: never read it whole; use the page index in PLAN.md and read only the pages you need.

## Hard constraints
- IMPORTANT: budget is 0 €. Only free tiers: Oracle Cloud Always Free, Hugging Face (public dataset), GitHub (public repo), Cloudflare Pages, Kaggle. Never add a paid service or something that only works with one. If a task seems to need money, stop and ask.
- The collector runs on its own Oracle Always Free VM (not the family-app VM, see below). Everything we ship must still: open no ports, cap CPU and memory, cap local disk use, restart on its own.
- Autonomy (owner, 2026-11-16): do everything you can yourself without asking; interrupt the owner only for something serious, a security problem, anything that could cost money, or a credential only they hold.
- Two Oracle VMs. `siwel-turn` hosts the owner's family video calls (coturn + siwel-push): **off-limits**, never deploy or change anything there. The collector runs on its own Always Free VM `tc-collector`, reached via `ssh tc-vm` (alias in `~/.ssh/config` on the dev machine, dedicated key; commands run without per-command approval). On `tc-collector` system setup is allowed (`bash scripts/vm.sh prepare`: Docker, swap); keep it dedicated to this project. No scp/rsync. Never write a VM IP in the repo. Keep `scripts/vm.sh` and `docs/DEPLOY.md` working so the owner can still do everything by hand.
- Remote resources: the owner allows creating free ones when we hold the means (e.g. a Cloud Shell script the owner runs). Never anything paid. Never ask for account-wide credentials (Oracle API keys, HF tokens beyond one dataset). Code reads names and tokens from env vars and never calls `create_repo`.
- ODbL: keep the attribution "Données SNCF Voyageurs, licence ODbL" in README and in the HF dataset card. The derived archive is published openly on HF on purpose (share-alike).
- Secrets only in `.env` (gitignored). Never print, log or commit them.

## Environment
- Dev machine: Windows 11, Claude Code in VS Code. The folder is synced by OneDrive and its path contains spaces: always quote paths. Use commands that work in the shell Claude Code runs on Windows (usually Git Bash); prefer `uv run python ...` over shell tricks.
- Target VM: Linux, architecture not guaranteed (Ampere arm64 or AMD x86_64): build the Docker image on the VM, never assume the arch.
- `.gitattributes` forces LF endings; keep shell scripts and Dockerfiles LF.

## Stack
Python 3.12, uv, httpx, gtfs-realtime-bindings, pyarrow, pydantic-settings, huggingface_hub, pytest, ruff. Later phases: duckdb, polars, lightgbm, mlflow (local), static site.
Ask before adding any other dependency.

## Layout
- `src/tc/ingest/` fetch, parse, reduce feeds
- `src/tc/store/` parquet writing, compaction, HF upload, retention
- `src/tc/common/` config, logging, clock
- `tests/` pytest, offline only; fixtures in `tests/fixtures/` (< 200 KB each)
- `docker/` Dockerfile, compose.yaml
- `docs/` PLAN.md, DEPLOY.md, DATA.md, `decisions/` (one short ADR per non-obvious choice)
- `data/`, `logs/` runtime only, gitignored

## Commands
- `uv sync`
- `uv run pytest -q` (single file: `uv run pytest tests/test_x.py -q`)
- `uv run ruff check --fix . && uv run ruff format .`

## How to work (token economy)
- Read only the files the task needs. Search with grep/glob before opening files. Never open `data/`, `logs/`, `.venv/`, `*.parquet`, `*.pb*`, `*.zip`.
- Keep command output short: `-q` flags, `| tail -n 40`.
- Small verified steps: write the test, implement, run it, fix. Show evidence (test output), not claims.
- Do not paste whole files back into the chat.
- If a fact is missing (URL, schema, behaviour of the feed), check `.claude/rules/` and `docs/` first, then ask. Do not browse the web unless asked.
- End every task by updating `PROGRESS.md` (≤ 25 lines: done, next, open questions) and give me a summary in French of ≤ 10 lines.
- Code, comments, commits: English. Talk to me in French, briefly.

## Compact instructions
When compacting, keep: the list of modified files, test commands and their last result, open decisions and the current task's done criteria.
