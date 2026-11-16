"""HTTP fetching: timeout, retries with exponential backoff + jitter, conditional GET."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx

from tc.common.config import Settings
from tc.common.log import get_logger

log = get_logger(__name__)
T = TypeVar("T")


class FetchError(Exception):
    """Final failure after all retries (or a non-retryable HTTP status)."""


class _Retryable(Exception):
    pass


@dataclass
class FetchResult:
    url: str
    status: str  # "ok" | "not_modified" | "error"
    content: bytes = b""
    error: str | None = None


class Fetcher:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ):
        self.retries = settings.http_retries
        self.backoff_s = settings.http_backoff_s
        self._sleep = sleep
        self._rand = rand
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_s),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self._validators: dict[str, dict[str, str]] = {}  # url -> ETag / Last-Modified

    def close(self) -> None:
        self._client.close()

    def _retry(self, op: Callable[[], T], url: str) -> T:
        last = "unknown"
        for attempt in range(self.retries + 1):
            try:
                return op()
            except _Retryable as exc:
                last = str(exc)
            except httpx.TransportError as exc:  # timeouts, DNS, connection reset...
                last = f"{type(exc).__name__}: {exc}"
            if attempt < self.retries:
                delay = self.backoff_s * (2**attempt) * (0.5 + self._rand() / 2)
                log.warning(
                    "fetch retry", extra={"url": url, "attempt": attempt + 1, "error": last}
                )
                self._sleep(delay)
        raise FetchError(last)

    @staticmethod
    def _check_status(status: int) -> None:
        if status == 429 or status >= 500:
            raise _Retryable(f"HTTP {status}")
        if status >= 400:
            raise FetchError(f"HTTP {status}")

    def get(self, url: str, conditional: bool = True) -> FetchResult:
        """GET with retries. Never raises: failures come back as status="error"."""
        headers: dict[str, str] = {}
        validators = self._validators.get(url, {}) if conditional else {}
        if "etag" in validators:
            headers["If-None-Match"] = validators["etag"]
        if "last-modified" in validators:
            headers["If-Modified-Since"] = validators["last-modified"]

        def op() -> httpx.Response:
            resp = self._client.get(url, headers=headers)
            if resp.status_code != 304:
                self._check_status(resp.status_code)
            return resp

        try:
            resp = self._retry(op, url)
        except FetchError as exc:
            return FetchResult(url, "error", error=str(exc))
        if resp.status_code == 304:
            return FetchResult(url, "not_modified")
        self._validators[url] = {
            k: resp.headers[k] for k in ("etag", "last-modified") if k in resp.headers
        }
        return FetchResult(url, "ok", content=resp.content)

    def download(self, url: str, dest: Path) -> tuple[str, int]:
        """Stream url to dest (atomic via .part). Returns (sha256 hex, size). Raises FetchError."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")

        def op() -> tuple[str, int]:
            sha, size = hashlib.sha256(), 0
            with self._client.stream("GET", url) as resp:
                self._check_status(resp.status_code)
                with part.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
                        sha.update(chunk)
                        size += len(chunk)
            return sha.hexdigest(), size

        try:
            result = self._retry(op, url)
        except FetchError:
            part.unlink(missing_ok=True)
            raise
        part.replace(dest)
        return result
