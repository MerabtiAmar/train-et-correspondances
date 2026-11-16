"""Push one finished day to the Hugging Face dataset, then (and only then) free the local copy.

The hub client is injectable so tests use a fake. This module never creates the dataset repo:
the owner creates it by hand (no remote resource is created by code).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from huggingface_hub import CommitOperationAdd, HfApi

from tc.common.config import Settings
from tc.common.log import get_logger
from tc.ingest.static_gtfs import KEEP_AFTER_UPLOAD
from tc.store.card import render_card
from tc.store.manifest import Manifest, day_files, rel_path

log = get_logger(__name__)


class HubClient(Protocol):
    def create_commit(self, **kwargs: Any) -> Any: ...
    def get_paths_info(self, **kwargs: Any) -> list[Any]: ...


@dataclass
class UploadResult:
    status: str  # "uploaded" | "dry_run" | "nothing" | "failed"
    day: date
    files: int = 0
    bytes: int = 0
    error: str | None = None


class Uploader:
    def __init__(self, settings: Settings, manifest: Manifest, client: HubClient | None = None):
        self.data_dir = settings.data_dir
        self.repo_id = settings.hf_repo.strip()
        self.manifest = manifest
        if client is None and not settings.dry_run:
            client = HfApi(token=settings.hf_token.get_secret_value())  # type: ignore[union-attr]
        self.client = client

    @property
    def dry_run(self) -> bool:
        return self.client is None

    def upload_day(self, day: date) -> UploadResult:
        files = day_files(self.data_dir, day)
        if not files:
            return UploadResult("nothing", day)
        total = sum(p.stat().st_size for p in files)
        if self.client is None:
            log.info(
                "dry-run: day kept locally (set HF_TOKEN and HF_REPO to upload)",
                extra={"day": day.isoformat(), "files": len(files), "bytes": total},
            )
            return UploadResult("dry_run", day, len(files), total)

        rels = {rel_path(self.data_dir, p): p for p in files}
        operations = [
            CommitOperationAdd(path_in_repo=rel, path_or_fileobj=str(p)) for rel, p in rels.items()
        ]
        operations.append(
            CommitOperationAdd(
                path_in_repo="README.md", path_or_fileobj=render_card(self.repo_id).encode()
            )
        )
        try:
            self.client.create_commit(
                repo_id=self.repo_id,
                repo_type="dataset",
                operations=operations,
                commit_message=f"Archive {day.isoformat()}",
            )
            remote = {
                info.path: info.size
                for info in self.client.get_paths_info(
                    repo_id=self.repo_id, paths=list(rels), repo_type="dataset"
                )
            }
        except Exception as exc:  # network, auth, missing repo... never lose local data
            return self._failed(day, f"{type(exc).__name__}: {exc}")

        unconfirmed = [r for r, p in rels.items() if remote.get(r) != p.stat().st_size]
        if unconfirmed:
            return self._failed(day, f"{len(unconfirmed)} file(s) not confirmed on the hub")

        for path in rels.values():
            self.manifest.add(path, path.stat().st_size)
        self.manifest.save()
        self._delete_local(list(rels.values()))
        log.info(
            "day uploaded", extra={"day": day.isoformat(), "files": len(files), "bytes": total}
        )
        return UploadResult("uploaded", day, len(files), total)

    def _failed(self, day: date, error: str) -> UploadResult:
        log.error(
            "upload failed, local files kept (will retry)",
            extra={"day": day.isoformat(), "error": error},
        )
        return UploadResult("failed", day, error=error)

    def _delete_local(self, paths: list[Path]) -> None:
        for path in paths:
            # The latest static trips/stops stay: they let the collector flag unknown ids.
            if path.parent.parent.name == "static" and path.name in KEEP_AFTER_UPLOAD:
                continue
            path.unlink(missing_ok=True)
        for path in paths:
            with contextlib.suppress(OSError):
                path.parent.rmdir()  # only succeeds when the folder is empty
        self._prune_old_static_index()

    def _prune_old_static_index(self) -> None:
        folders = sorted((self.data_dir / "static").glob("date=*"))
        for folder in folders[:-1]:  # keep the index of the newest static folder only
            for name in KEEP_AFTER_UPLOAD:
                if self.manifest.is_uploaded(folder / name):
                    (folder / name).unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                folder.rmdir()
