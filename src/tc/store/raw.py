"""Raw samples: one gzipped protobuf snapshot per feed every N minutes, for reprocessing.

data/raw/date=YYYY-MM-DD/<feed>_<YYYYMMDDTHHMMSSZ>.pb.gz  (date = Europe/Paris date)
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

from tc.common.clock import paris_date
from tc.store.files import write_bytes_atomic

_STAMP = "%Y%m%dT%H%M%SZ"


class RawSampler:
    def __init__(self, data_dir: Path, every_min: int):
        self.data_dir = data_dir
        self.every_s = max(1, every_min) * 60
        self._last_bucket: dict[str, int] = {}

    def _folder(self, now: datetime) -> Path:
        return self.data_dir / "raw" / f"date={paris_date(now).isoformat()}"

    def _bucket(self, ts: datetime) -> int:
        return int(ts.timestamp() // self.every_s)

    def _restore(self, feed: str, now: datetime) -> None:
        """After a restart, the sample already on disk for this interval counts."""
        names = [p.name for p in self._folder(now).glob(f"{feed}_*.pb.gz")]
        if not names:
            return
        stamp = max(names).removeprefix(f"{feed}_").removesuffix(".pb.gz")
        try:
            ts = datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)
        except ValueError:
            return
        self._last_bucket[feed] = self._bucket(ts)

    def due(self, feed: str, now: datetime) -> bool:
        if feed not in self._last_bucket:
            self._restore(feed, now)
        return self._bucket(now) != self._last_bucket.get(feed)

    def save(self, feed: str, content: bytes, now: datetime) -> Path:
        path = self._folder(now) / f"{feed}_{now.strftime(_STAMP)}.pb.gz"
        write_bytes_atomic(path, gzip.compress(content, compresslevel=6, mtime=0))
        self._last_bucket[feed] = self._bucket(now)
        return path
