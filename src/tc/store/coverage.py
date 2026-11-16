"""Daily coverage report: how complete is the archive, and what was wrong with the feed."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq

from tc.common.clock import PARIS, day_length_s
from tc.store.manifest import day_files, rel_path

GAP_THRESHOLD_S = 6 * 60


@dataclass
class FeedStats:
    fetch_ok: int = 0
    fetch_errors: int = 0
    not_modified: int = 0  # HTTP 304
    unchanged_skipped: int = 0  # header timestamp did not change
    empty: int = 0
    stale: int = 0
    parse_errors: int = 0
    sources: dict[str, int] = field(default_factory=dict)  # url actually used -> snapshots
    snapshot_ts: list[int] = field(default_factory=list)  # distinct header timestamps ingested


@dataclass
class DayStats:
    day: str
    polls_done: int = 0
    polls_skipped_overrun: int = 0
    trip_updates: FeedStats = field(default_factory=FeedStats)
    alerts: FeedStats = field(default_factory=FeedStats)
    defects: dict[str, int] = field(default_factory=dict)

    def add_defects(self, counts: Counter) -> None:
        for name, n in counts.items():
            self.defects[name] = self.defects.get(name, 0) + n

    @classmethod
    def from_dict(cls, data: dict) -> DayStats:
        return cls(
            day=data["day"],
            polls_done=data.get("polls_done", 0),
            polls_skipped_overrun=data.get("polls_skipped_overrun", 0),
            trip_updates=FeedStats(**data.get("trip_updates", {})),
            alerts=FeedStats(**data.get("alerts", {})),
            defects=data.get("defects", {}),
        )


def stats_path(data_dir: Path, day: date) -> Path:
    return data_dir / ".state" / f"stats-{day.isoformat()}.json"


def save_stats(data_dir: Path, stats: DayStats) -> None:
    path = stats_path(data_dir, date.fromisoformat(stats.day))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(asdict(stats)), encoding="utf-8")
    os.replace(tmp, path)


def load_stats(data_dir: Path, day: date) -> DayStats:
    """Stats of a day, resumed after a restart; empty if there are none."""
    try:
        return DayStats.from_dict(json.loads(stats_path(data_dir, day).read_text("utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return DayStats(day=day.isoformat())


def find_gaps(timestamps: list[int], start: int | None, end: int | None) -> list[dict]:
    """Holes longer than 6 minutes between consecutive snapshots (and day edges if given)."""
    points = sorted(set(timestamps))
    if not points:
        return [_gap(start, end)] if start is not None and end is not None else []
    if start is not None:
        points.insert(0, start)
    if end is not None:
        points.append(end)
    return [_gap(a, b) for a, b in zip(points, points[1:], strict=False) if b - a > GAP_THRESHOLD_S]


def _gap(a: int, b: int) -> dict:
    return {
        "from": datetime.fromtimestamp(a, UTC).isoformat(timespec="seconds"),
        "to": datetime.fromtimestamp(b, UTC).isoformat(timespec="seconds"),
        "minutes": round((b - a) / 60, 1),
    }


def _feed_report(f: FeedStats, gaps: list[dict] | None = None) -> dict:
    report = {
        "distinct_snapshots": len(set(f.snapshot_ts)),
        "fetch_ok": f.fetch_ok,
        "fetch_errors": f.fetch_errors,
        "not_modified": f.not_modified,
        "unchanged_skipped": f.unchanged_skipped,
        "empty_feeds": f.empty,
        "stale_feeds": f.stale,
        "parse_errors": f.parse_errors,
        "sources": f.sources,
    }
    if gaps is not None:
        report["gaps_over_6_min"] = gaps
    return report


def build_report(
    data_dir: Path, stats: DayStats, poll_seconds: int, complete: bool, now: datetime
) -> dict:
    day = date.fromisoformat(stats.day)
    midnight = datetime(day.year, day.month, day.day, tzinfo=PARIS)
    start = int(midnight.timestamp())
    end = start + day_length_s(day)
    if not complete:
        end = min(end, int(now.timestamp()))
    files = [p for p in day_files(data_dir, day) if p.name != "coverage.json"]
    rows: dict[str, int] = {}
    for kind in ("reduced", "alerts"):
        rows[kind] = sum(
            pq.ParquetFile(p).metadata.num_rows
            for p in files
            if p.suffix == ".parquet" and rel_path(data_dir, p).startswith(f"{kind}/")
        )
    return {
        "day": stats.day,
        "timezone": "Europe/Paris",
        "complete": complete,
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "poll_seconds": poll_seconds,
        "polls_expected": (end - start) // poll_seconds,
        "polls_done": stats.polls_done,
        "polls_skipped_overrun": stats.polls_skipped_overrun,
        "trip_updates": _feed_report(
            stats.trip_updates, find_gaps(stats.trip_updates.snapshot_ts, start, end)
        ),
        "alerts": _feed_report(stats.alerts),
        "defects": dict(sorted(stats.defects.items())),
        "rows_written": rows,
        "bytes_per_file": {rel_path(data_dir, p): p.stat().st_size for p in files},
    }


def write_report(data_dir: Path, report: dict) -> Path:
    path = data_dir / "coverage" / f"date={report['day']}" / "coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path
