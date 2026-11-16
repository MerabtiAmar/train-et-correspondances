import hashlib

import httpx
import pytest

from tc.common.config import Settings
from tc.ingest.fetch import Fetcher, FetchError

URL = "https://example.test/feed"


def _fetcher(handler, **overrides) -> tuple[Fetcher, list[float]]:
    sleeps: list[float] = []
    settings = Settings(_env_file=None, contact_email="ops@example.org", **overrides)
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": settings.user_agent}
    )
    return Fetcher(settings, client=client, sleep=sleeps.append, rand=lambda: 1.0), sleeps


def test_retries_5xx_with_exponential_backoff_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, content=b"pb")

    f, sleeps = _fetcher(handler)
    res = f.get(URL)
    assert res.status == "ok" and res.content == b"pb"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # backoff doubles; jitter pinned to factor 1.0
    assert "ops@example.org" in calls[0].headers["user-agent"]


def test_gives_up_after_three_retries():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectTimeout("boom")

    f, sleeps = _fetcher(handler)
    res = f.get(URL)
    assert res.status == "error" and "ConnectTimeout" in res.error
    assert len(calls) == 4  # 1 try + 3 retries
    assert len(sleeps) == 3


def test_jitter_stays_within_half_to_full_delay():
    f, sleeps = _fetcher(lambda r: httpx.Response(500))
    f._rand = lambda: 0.0
    f.get(URL)
    assert sleeps == [0.5, 1.0, 2.0]


def test_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    f, sleeps = _fetcher(handler)
    assert f.get(URL).status == "error"
    assert len(calls) == 1 and sleeps == []


def test_conditional_get_uses_validators_and_handles_304():
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"x", headers={"ETag": '"v1"'})

    f, _ = _fetcher(handler)
    assert f.get(URL).status == "ok"
    assert "if-none-match" not in seen[0]
    assert f.get(URL).status == "not_modified"
    assert f.get(URL, conditional=False).status == "ok"  # forced unconditional


def test_download_streams_to_file_and_hashes(tmp_path):
    body = b"zip-bytes" * 1000
    f, _ = _fetcher(lambda r: httpx.Response(200, content=body))
    dest = tmp_path / "sub" / "g.zip"
    sha, size = f.download(URL, dest)
    assert sha == hashlib.sha256(body).hexdigest() and size == len(body)
    assert dest.read_bytes() == body
    assert not list(dest.parent.glob("*.part"))


def test_download_failure_leaves_no_partial_file(tmp_path):
    f, _ = _fetcher(lambda r: httpx.Response(500))
    dest = tmp_path / "g.zip"
    with pytest.raises(FetchError):
        f.download(URL, dest)
    assert list(tmp_path.iterdir()) == []
