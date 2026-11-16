"""Record of files confirmed on the Hugging Face dataset: path (relative to data/) -> size.

Retention only ever deletes files listed here, with the size they had when uploaded.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

DAY_KINDS = ("reduced", "alerts", "raw", "coverage", "static")


def rel_path(data_dir: Path, path: Path) -> str:
    return path.relative_to(data_dir).as_posix()


class Manifest:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / ".state" / "uploaded.json"
        try:
            self._sizes: dict[str, int] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._sizes = {}

    def is_uploaded(self, path: Path) -> bool:
        """True only if this exact file (same size) was confirmed on the hub."""
        try:
            return self._sizes.get(rel_path(self.data_dir, path)) == path.stat().st_size
        except OSError:
            return False

    def add(self, path: Path, size: int) -> None:
        self._sizes[rel_path(self.data_dir, path)] = size

    def prune_missing(self) -> None:
        """Forget entries whose local file is gone (keeps the manifest small)."""
        self._sizes = {r: s for r, s in self._sizes.items() if (self.data_dir / r).exists()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self._sizes, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


def day_files(data_dir: Path, day: date) -> list[Path]:
    """Every collected file belonging to a Paris calendar day (hidden temp files excluded)."""
    files: list[Path] = []
    for kind in DAY_KINDS:
        folder = data_dir / kind / f"date={day.isoformat()}"
        if folder.is_dir():
            files += [p for p in sorted(folder.rglob("*")) if p.is_file() and p.name[0] != "."]
    return files


def pending_days(data_dir: Path, today: date, manifest: Manifest) -> list[date]:
    """Past days that still hold local files not confirmed on the hub, oldest first."""
    days: set[date] = set()
    for kind in DAY_KINDS:
        for folder in (data_dir / kind).glob("date=*"):
            try:
                day = date.fromisoformat(folder.name.removeprefix("date="))
            except ValueError:
                continue
            if day < today and any(not manifest.is_uploaded(p) for p in day_files(data_dir, day)):
                days.add(day)
    return sorted(days)
