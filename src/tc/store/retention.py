"""Disk cap for data/. Only files confirmed on the hub can ever be deleted.

Order: uploaded raw samples first (oldest first), then any other uploaded file, oldest first.
If the cap is still exceeded, nothing else is touched: an error is logged and `blocked` is set
so the caller can stop writing the least valuable data (raw samples) instead of losing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tc.common.log import get_logger
from tc.store.files import dir_size
from tc.store.manifest import Manifest, rel_path

log = get_logger(__name__)


@dataclass
class RetentionResult:
    bytes_before: int
    bytes_after: int
    deleted: list[str] = field(default_factory=list)
    blocked: bool = False


def _order(data_dir: Path, path: Path) -> tuple[int, str, str]:
    rel = rel_path(data_dir, path)
    date_part = next((p for p in rel.split("/") if p.startswith("date=")), "")
    return (0 if rel.startswith("raw/") else 1, date_part, rel)


def enforce_retention(data_dir: Path, max_mb: int, manifest: Manifest) -> RetentionResult:
    cap = max_mb * 1024 * 1024
    before = size = dir_size(data_dir)
    result = RetentionResult(before, before)
    if size <= cap:
        return result

    candidates = sorted(
        (
            p
            for p in data_dir.rglob("*")
            if p.is_file() and ".state" not in p.parts and manifest.is_uploaded(p)
        ),
        key=lambda p: _order(data_dir, p),
    )
    for path in candidates:
        if size <= cap:
            break
        freed = path.stat().st_size
        path.unlink()
        size -= freed
        result.deleted.append(rel_path(data_dir, path))
    if result.deleted:
        manifest.prune_missing()
        manifest.save()
        log.warning(
            "retention deleted uploaded files",
            extra={"files": len(result.deleted), "first": result.deleted[0]},
        )
    result.bytes_after = size
    if size > cap:
        result.blocked = True
        log.error(
            "disk cap exceeded and nothing left that is safe to delete (not uploaded yet)",
            extra={"used_mb": round(size / 2**20, 1), "max_mb": max_mb},
        )
    return result
