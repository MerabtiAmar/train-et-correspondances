"""Static GTFS: download daily, keep only when the sha256 changed, convert to Parquet.

The conversion streams CSV batches into Parquet, so the large stop_times.txt never has to fit
in memory (the container is capped at 768 MB).

Layout: data/static/date=YYYY-MM-DD/{gtfs.zip,trips,stop_times,stops,routes,calendar_dates}.*
State:  data/.state/static.json {sha256, checked_date}
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from tc.common.clock import paris_date
from tc.common.log import get_logger
from tc.ingest.fetch import Fetcher, FetchError

log = get_logger(__name__)

TABLES = ("trips", "stop_times", "stops", "routes", "calendar_dates")
# Everything is kept as text (ids, times like "25:10:00") except these.
INT_COLUMNS = {"stop_sequence", "route_type", "exception_type", "direction_id", "location_type"}
KEEP_AFTER_UPLOAD = ("trips.parquet", "stops.parquet")  # used to flag unknown ids


@dataclass
class StaticResult:
    status: str  # "changed" | "unchanged" | "error"
    sha256: str | None = None
    folder: Path | None = None
    error: str | None = None


def _state_path(data_dir: Path) -> Path:
    return data_dir / ".state" / "static.json"


def load_state(data_dir: Path) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(data_dir: Path, state: dict) -> None:
    path = _state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def convert_table(zf: zipfile.ZipFile, name: str, out_path: Path) -> int:
    """Stream <name>.txt from the zip into out_path (atomic). Returns the row count."""
    member = f"{name}.txt"
    with zf.open(member) as fh:
        header_line = io.TextIOWrapper(fh, encoding="utf-8-sig").readline()
    columns = next(csv.reader([header_line.rstrip("\r\n")]))
    types = {c: pa.int32() if c in INT_COLUMNS else pa.string() for c in columns}
    tmp = out_path.with_name(f".{out_path.name}.tmp")
    rows = 0
    try:
        with zf.open(member) as fh:
            reader = pacsv.open_csv(
                fh,
                read_options=pacsv.ReadOptions(block_size=1 << 22),
                convert_options=pacsv.ConvertOptions(column_types=types, strings_can_be_null=True),
            )
            with pq.ParquetWriter(tmp, reader.schema, compression="zstd") as writer:
                for batch in reader:
                    writer.write_batch(batch)
                    rows += batch.num_rows
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return rows


def refresh_static(data_dir: Path, fetcher: Fetcher, url: str, now: datetime) -> StaticResult:
    """One daily check. Never raises; a failure leaves the previous state untouched."""
    day = paris_date(now)
    state = load_state(data_dir)
    folder = data_dir / "static" / f"date={day.isoformat()}"
    download = data_dir / "static" / ".download.zip"
    try:
        sha, size = fetcher.download(url, download)
    except FetchError as exc:
        return StaticResult("error", error=f"download failed: {exc}")

    if sha == state.get("sha256"):
        download.unlink(missing_ok=True)
        save_state(data_dir, {**state, "checked_date": day.isoformat()})
        log.info("static GTFS unchanged", extra={"sha256": sha[:12], "bytes": size})
        return StaticResult("unchanged", sha256=sha)

    folder.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(download) as zf:
            counts = {}
            for name in TABLES:
                if f"{name}.txt" not in zf.namelist():
                    log.warning("static GTFS member missing", extra={"table": name})
                    continue
                counts[name] = convert_table(zf, name, folder / f"{name}.parquet")
        os.replace(download, folder / "gtfs.zip")
    except (zipfile.BadZipFile, OSError, pa.ArrowException, ValueError, KeyError) as exc:
        download.unlink(missing_ok=True)
        for part in folder.glob("*.parquet"):
            part.unlink(missing_ok=True)
        return StaticResult("error", sha256=sha, error=f"conversion failed: {exc}")

    save_state(data_dir, {"sha256": sha, "checked_date": day.isoformat()})
    log.info("static GTFS updated", extra={"sha256": sha[:12], "bytes": size, "rows": counts})
    return StaticResult("changed", sha256=sha, folder=folder)


@dataclass
class StaticIndex:
    trip_ids: frozenset[str]
    stop_ids: frozenset[str]

    def flag(self, rows: list[dict]) -> None:
        """Add TRIP_NOT_IN_STATIC / STOP_NOT_IN_STATIC (E003 / E011) to rows, in place."""
        for row in rows:
            if row["trip_id"] and row["trip_id"] not in self.trip_ids:
                row["defect_flags"].append("TRIP_NOT_IN_STATIC")
            if row["stop_id"] and row["stop_id"] not in self.stop_ids:
                row["defect_flags"].append("STOP_NOT_IN_STATIC")


def load_static_index(data_dir: Path) -> StaticIndex | None:
    """Index of the latest static GTFS, or None if it is not available locally."""
    for folder in sorted((data_dir / "static").glob("date=*"), reverse=True):
        trips, stops = folder / "trips.parquet", folder / "stops.parquet"
        if trips.exists() and stops.exists():
            return StaticIndex(
                frozenset(pq.read_table(trips, columns=["trip_id"])["trip_id"].to_pylist()),
                frozenset(pq.read_table(stops, columns=["stop_id"])["stop_id"].to_pylist()),
            )
    return None
