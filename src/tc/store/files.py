"""Small filesystem helpers shared by the store modules."""

from __future__ import annotations

import os
from pathlib import Path


def write_bytes_atomic(path: Path, data: bytes) -> int:
    """Temp file in the same folder + fsync + rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")  # hidden: cleaned by parquet.cleanup_tmp
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(data)


def dir_size(root: Path) -> int:
    """Total size in bytes of all files under root (0 if it does not exist)."""
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.exists() else 0
