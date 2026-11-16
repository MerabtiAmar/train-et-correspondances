# Train & Correspondances

Predict, before booking, the probability that a French train is late or cancelled and that a
rail connection holds, as calibrated probabilities.

Données SNCF Voyageurs, licence ODbL.

This repository currently holds **Phase 0: the GTFS-RT collector**. The real-time feed only covers
the next 60 minutes, so every day that is not archived is lost: the collector polls Trip Updates
and Service Alerts every 2 minutes, reduces them at source, and publishes one daily archive to a
public Hugging Face dataset (share-alike, as required by the ODbL).

- Status and next step: [PROGRESS.md](PROGRESS.md)
- Data schemas and keys: [docs/DATA.md](docs/DATA.md)
- Deploy on the Oracle VM (French): [docs/DEPLOY.md](docs/DEPLOY.md)
- Plan: [docs/PLAN.md](docs/PLAN.md); decisions: [docs/decisions/](docs/decisions/)

## Develop

```
uv sync
uv run pytest -q
uv run ruff check --fix . && uv run ruff format .
cp .env.example .env        # then edit
uv run python -m tc         # run the collector (RUN_FOR_MINUTES=10 for a short test)
```

Independent project, not affiliated with SNCF.
