"""In-memory reduction: keep the latest row per key, plus first/last seen and an update count.

"Latest" means the highest feed timestamp, not the last one received, so a late or replayed
snapshot never overwrites newer data (it only extends first_seen_ts / n_updates).
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]


class Reducer:
    def __init__(self, key_fields: tuple[str, ...]):
        self.key_fields = key_fields
        self._rows: dict[tuple, Row] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[Row]:
        return list(self._rows.values())

    def load(self, rows: list[Row]) -> None:
        """Restore state from previously written rows (already carry their aggregates)."""
        for row in rows:
            self._rows[self._key(row)] = row

    def _key(self, row: Row) -> tuple:
        return tuple(row[k] for k in self.key_fields)

    @staticmethod
    def _replace_payload(cur: Row, new: Row) -> None:
        for name, value in new.items():  # new has no aggregate fields: they are preserved
            cur[name] = value

    def add_snapshot(self, rows: list[Row]) -> int:
        """Fold one snapshot in. Returns the number of duplicate-key rows inside the snapshot.

        A duplicate (same key twice in the same snapshot, defects E036/E009) is kept as the
        latest value, flagged DUP_KEY, and does not count as an extra update.
        """
        seen: set[tuple] = set()
        dups = 0
        for row in rows:
            key = self._key(row)
            cur = self._rows.get(key)
            ts = row["feed_ts"]
            if key in seen:
                dups += 1
                if "DUP_KEY" not in row["defect_flags"]:
                    row = {**row, "defect_flags": [*row["defect_flags"], "DUP_KEY"]}
                if ts >= cur["feed_ts"]:
                    self._replace_payload(cur, row)
                continue
            seen.add(key)
            if cur is None:
                self._rows[key] = {**row, "first_seen_ts": ts, "last_seen_ts": ts, "n_updates": 1}
                continue
            cur["n_updates"] += 1
            cur["first_seen_ts"] = min(cur["first_seen_ts"], ts)
            cur["last_seen_ts"] = max(cur["last_seen_ts"], ts)
            if ts >= cur["feed_ts"]:
                self._replace_payload(cur, row)
        return dups
