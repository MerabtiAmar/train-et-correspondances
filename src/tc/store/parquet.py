"""Hourly Parquet files (atomic rewrite of the current hour) and daily compaction.

Layout: data/<kind>/date=YYYY-MM-DD/<YYYYMMDDTHHZ>.parquet (hourly, UTC hour in the name)
        data/<kind>/date=YYYY-MM-DD/day.parquet            (after compaction)
`date` is the Europe/Paris calendar date of the hour; the hour label stays UTC so that it is
unambiguous on DST change days.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from tc.common.clock import hour_stamp, hour_start, paris_date
from tc.common.log import get_logger
from tc.ingest.reduce import Reducer, Row
from tc.store.schema import KINDS, TS, epoch_seconds, rows_to_table, table_to_rows

log = get_logger(__name__)

HOURLY_GLOB = "[0-9]*T[0-9][0-9]Z.parquet"
DAY_FILE = "day.parquet"


def day_dir(data_dir: Path, kind: str, day: date) -> Path:
    return data_dir / kind / f"date={day.isoformat()}"


def hourly_path(data_dir: Path, kind: str, hour: datetime) -> Path:
    return day_dir(data_dir, kind, paris_date(hour)) / f"{hour_stamp(hour)}.parquet"


def write_table_atomic(table: pa.Table, path: Path) -> int:
    """Write to a hidden temp file in the same folder, fsync, then rename. Returns bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        pq.write_table(table, tmp, compression="zstd")
        with tmp.open("rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path.stat().st_size


def cleanup_tmp(data_dir: Path) -> int:
    """Remove temp files left by a crash in the middle of a write."""
    n = 0
    for tmp in data_dir.rglob(".*.tmp"):
        tmp.unlink(missing_ok=True)
        n += 1
    return n


class HourlyStore:
    """Reducer + the Parquet file of the hour currently being collected."""

    def __init__(self, data_dir: Path, kind: str):
        self.data_dir = data_dir
        self.kind = kind
        self.schema, self.key = KINDS[kind]
        self.reducer = Reducer(self.key)
        self.hour: datetime | None = None
        self.dirty = False
        self.rows_in_file = 0

    def _open(self, hour: datetime) -> None:
        """Start an hour. If a file already exists (restart), resume from it: no data lost."""
        self.hour = hour
        self.reducer = Reducer(self.key)
        self.dirty = False
        path = hourly_path(self.data_dir, self.kind, hour)
        if path.exists():
            self.reducer.load(table_to_rows(pq.read_table(path)))
            log.info(
                "resumed hour",
                extra={"kind": self.kind, "file": path.name, "rows": len(self.reducer)},
            )

    def ingest(self, now: datetime, rows: list[Row]) -> int:
        """Fold a snapshot into the hour containing `now`. Returns duplicate-key count."""
        hour = hour_start(now)
        if self.hour != hour:
            self.flush()
            self._open(hour)
        dups = self.reducer.add_snapshot(rows)
        self.dirty = True
        return dups

    def flush(self) -> Path | None:
        if self.hour is None or not self.dirty:
            return None
        path = hourly_path(self.data_dir, self.kind, self.hour)
        size = write_table_atomic(rows_to_table(self.reducer.rows(), self.schema), path)
        self.dirty = False
        self.rows_in_file = len(self.reducer)
        log.info(
            "hourly file written",
            extra={"kind": self.kind, "file": path.name, "rows": len(self.reducer), "bytes": size},
        )
        return path


# A row's "rank" packs (feed_ts, input file, row) into one int64, so that the max rank of a key
# is its latest version, ties going to the later file then the later row. feed_ts keeps 33 bits
# (valid until the year 2242), file 6 bits (64 inputs), row 24 bits (16.7 M rows per file).
_ROW_BITS = 24
_FILE_BITS = 6
_AGG_COLUMNS = ("feed_ts", "first_seen_ts", "last_seen_ts", "n_updates")


def _winners(paths: list[Path], key_fields: tuple[str, ...]) -> pa.Table:
    """One row per key: rank of its latest version and the merged aggregates.

    Only the key and aggregate columns are read and everything stays in Arrow (hash
    group-by): a first version that went through Python objects peaked at 1.2 GB on a busy
    day, far above the container's 768 MB cap.
    """
    if len(paths) > 1 << _FILE_BITS:
        raise ValueError(f"too many files to merge: {len(paths)}")
    # Text keys are read dictionary-encoded (int32 codes instead of strings): ~5x less memory.
    first_schema = pq.read_schema(paths[0])
    text_keys = [k for k in key_fields if pa.types.is_string(first_schema.field(k).type)]
    parts = []
    for i, path in enumerate(paths):
        t = pq.read_table(path, columns=[*key_fields, *_AGG_COLUMNS], read_dictionary=text_keys)
        if t.num_rows >= 1 << _ROW_BITS:
            raise ValueError(f"{path.name} has too many rows to merge: {t.num_rows}")
        rows = pa.array(range(t.num_rows), pa.int64())
        rank = pc.add(
            pc.multiply(epoch_seconds(t["feed_ts"]), 1 << (_FILE_BITS + _ROW_BITS)),
            pc.add(rows, i << _ROW_BITS),
        )
        cols = {k: t[k] for k in key_fields}
        cols |= {
            "rank": rank,
            "first": epoch_seconds(t["first_seen_ts"]),
            "last": epoch_seconds(t["last_seen_ts"]),
            "n": t["n_updates"].cast(pa.int64()),
        }
        parts.append(pa.table(cols))
    # Each file has its own dictionaries; group-by needs one shared dictionary per column.
    narrow = pa.concat_tables(parts).unify_dictionaries()
    del parts
    win = narrow.group_by(list(key_fields), use_threads=False).aggregate(
        [("rank", "max"), ("first", "min"), ("last", "max"), ("n", "sum")]
    )
    return win.select(["rank_max", "first_min", "last_max", "n_sum"])


def merge_files(paths: list[Path], key_fields: tuple[str, ...], out_path: Path) -> int:
    """Merge Parquet files into out_path (atomic): latest feed_ts wins per key, later input
    wins ties; first_seen=min, last_seen=max, n_updates=sum. Returns the row count.

    The winning rows are copied file by file, so only one input is fully loaded at a time.
    """
    win = _winners(paths, key_fields)
    file_idx = pc.shift_right(win["rank_max"], _ROW_BITS)
    file_idx = pc.bit_wise_and(file_idx, (1 << _FILE_BITS) - 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f".{out_path.name}.tmp")
    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None
    total = 0
    try:
        for i, path in enumerate(paths):
            sel = win.filter(pc.equal(file_idx, i))
            if sel.num_rows == 0:
                continue
            rows = pc.bit_wise_and(sel["rank_max"], (1 << _ROW_BITS) - 1)
            order = pc.sort_indices(rows)  # keep the file's own row order
            sel = sel.take(order)
            chunk = pq.read_table(path).take(rows.take(order))
            for name, src, typ in (
                ("first_seen_ts", "first_min", TS),
                ("last_seen_ts", "last_max", TS),
                ("n_updates", "n_sum", pa.int32()),
            ):
                pos = chunk.schema.get_field_index(name)
                values = sel[src].cast(typ).cast(chunk.schema.field(name).type)
                chunk = chunk.set_column(pos, name, values)
            if writer is None:
                schema = chunk.schema
                writer = pq.ParquetWriter(tmp, schema, compression="zstd")
            elif not chunk.schema.equals(schema):
                chunk = chunk.cast(schema)
            writer.write_table(chunk)
            total += chunk.num_rows
        if writer is None:  # only empty inputs
            pq.write_table(pq.read_schema(paths[0]).empty_table(), tmp, compression="zstd")
        else:
            writer.close()
            writer = None
        with tmp.open("rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, out_path)
    except BaseException:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)
        raise
    return total


def compact_day(data_dir: Path, kind: str, day: date) -> Path | None:
    """Merge the hourly files of a finished day into day.parquet, then delete them.

    Safe to re-run: an existing day.parquet is merged back in, and hourly files are only
    deleted once the merged file is safely on disk.
    """
    folder = day_dir(data_dir, kind, day)
    hourly = sorted(folder.glob(HOURLY_GLOB))
    if not hourly:
        return None
    day_file = folder / DAY_FILE
    inputs = ([day_file] if day_file.exists() else []) + hourly
    rows = merge_files(inputs, KINDS[kind][1], day_file)
    for path in hourly:
        path.unlink()
    log.info(
        "day compacted",
        extra={
            "kind": kind,
            "day": day.isoformat(),
            "rows": rows,
            "bytes": day_file.stat().st_size,
        },
    )
    return day_file
