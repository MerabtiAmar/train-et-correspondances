"""Download one snapshot per GTFS-RT feed and trim each to < 200 KB for use as test fixtures.

Usage: uv run python scripts/record_fixture.py
Writes tests/fixtures/{trip_updates,alerts}.pb and tests/fixtures/fixtures.json (provenance).
Trimming keeps the header untouched and the first N entities, so the file stays a valid feed.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from tc.common.config import Settings
from tc.ingest.fetch import Fetcher

MAX_BYTES = 190_000  # stay clearly under the 200 KB limit
OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def trim(msg: gtfs_realtime_pb2.FeedMessage, max_bytes: int) -> gtfs_realtime_pb2.FeedMessage:
    """Keep the largest prefix of entities whose serialized size is <= max_bytes."""
    entities = list(msg.entity)
    lo, hi = 0, len(entities)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        probe = gtfs_realtime_pb2.FeedMessage()
        probe.header.CopyFrom(msg.header)
        probe.entity.extend(entities[:mid])
        if probe.ByteSize() <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    out = gtfs_realtime_pb2.FeedMessage()
    out.header.CopyFrom(msg.header)
    out.entity.extend(entities[:lo])
    return out


def fetch_feed(fetcher: Fetcher, urls: list[str], require_entities: bool):
    for url in urls:
        res = fetcher.get(url, conditional=False)
        if res.status != "ok":
            print(f"  {url}: {res.error}", file=sys.stderr)
            continue
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(res.content)
        if msg.entity or not require_entities:
            return url, len(res.content), msg
        print(f"  {url}: empty feed", file=sys.stderr)
    return None


def main() -> int:
    settings = Settings()
    fetcher = Fetcher(settings)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {"recorded_at": datetime.now(UTC).isoformat(timespec="seconds")}
    feeds = {
        "trip_updates": ([settings.trip_updates_url, settings.trip_updates_url_fallback], True),
        # An empty alerts feed is legitimate, so it is accepted as a fixture too.
        "alerts": ([settings.alerts_url], False),
    }
    status = 0
    for name, (urls, require) in feeds.items():
        got = fetch_feed(fetcher, urls, require)
        if got is None:
            print(f"FAILED: no usable {name} feed right now. Retry later.", file=sys.stderr)
            status = 1
            continue
        url, raw_size, msg = got
        trimmed = trim(msg, MAX_BYTES)
        path = OUT_DIR / f"{name}.pb"
        path.write_bytes(trimmed.SerializeToString())
        meta[name] = {
            "source_url": url,
            "feed_timestamp": msg.header.timestamp,
            "entities_total": len(msg.entity),
            "entities_kept": len(trimmed.entity),
            "raw_bytes": raw_size,
            "fixture_bytes": path.stat().st_size,
        }
        print(f"{name}: {len(trimmed.entity)}/{len(msg.entity)} entities, {path.stat().st_size} B")
    (OUT_DIR / "fixtures.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
