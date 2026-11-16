# 0003 — Upload confirmation, retention, and what happens when the disk is full

Status: accepted (session 1)

## Decision
- The uploader **never calls `create_repo`**. The owner creates the dataset by hand (project rule: no remote
  resource is created by code). A missing repo is a logged upload failure, local files kept.
- One commit per day (files + regenerated `README.md` card). "Confirmed" = the commit returned **and**
  `get_paths_info` shows every pushed path with the same size as the local file. Only then are the files
  recorded in `data/.state/uploaded.json` and deleted locally. Any doubt keeps the files; retry in 1 hour.
- Retention (`MAX_LOCAL_MB`) deletes only files listed in that manifest **with the size they had when
  uploaded**: uploaded raw samples first (oldest first), then other uploaded files, oldest first.
  The prompt's "oldest raw samples first" is applied to uploaded ones only, because "never delete data that
  has not been uploaded" wins.
- If the cap is exceeded and nothing is deletable (typical in dry-run, or when Hugging Face is down for a
  long time), an ERROR is logged and **raw samples are paused**; the reduced data keeps being written.
  Raw samples are the largest and least valuable data; the reduced rows are the irreplaceable ones.
- The latest static `trips.parquet` / `stops.parquet` stay on disk after upload (a few MB): they feed the
  `TRIP_NOT_IN_STATIC` / `STOP_NOT_IN_STATIC` flags.
- The dataset card is regenerated at each upload: manual edits of the card on the Hub are overwritten.

## Revisit if
The disk fills up in dry-run before the owner has created the token (the cap then protects the VM but the
data stops growing only in raw samples; the reduced files still add ~tens of MB per day).
