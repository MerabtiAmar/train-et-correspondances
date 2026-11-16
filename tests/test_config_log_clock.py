import json
import logging
from datetime import UTC, date, datetime

from tc.common.clock import Ticker, day_length_s, hour_stamp, paris_date
from tc.common.config import Settings
from tc.common.log import JsonFormatter


def _settings(monkeypatch, **env) -> Settings:
    for key in ("HF_TOKEN", "HF_REPO", "POLL_SECONDS", "CONTACT_EMAIL", "RUN_FOR_MINUTES"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults(monkeypatch):
    s = _settings(monkeypatch)
    assert s.poll_seconds == 120
    assert s.run_for_minutes == 0
    assert s.raw_sample_every_min == 60
    assert s.max_local_mb == 2048
    assert s.trip_updates_url.startswith("https://proxy.transport.data.gouv.fr/")
    assert s.dry_run is True


def test_env_overrides_and_blank_token_is_dry_run(monkeypatch):
    s = _settings(monkeypatch, POLL_SECONDS="30", HF_TOKEN="  ", HF_REPO="me/ds")
    assert s.poll_seconds == 30
    assert s.hf_token is None
    assert s.dry_run is True


def test_token_is_secret_and_enables_upload(monkeypatch):
    s = _settings(monkeypatch, HF_TOKEN="hf_secret_value", HF_REPO="me/ds")
    assert s.dry_run is False
    assert "hf_secret_value" not in repr(s)
    assert "hf_secret_value" not in str(s.model_dump())


def test_user_agent_contains_contact(monkeypatch):
    s = _settings(monkeypatch, CONTACT_EMAIL="ops@example.org")
    assert "train-correspondances-collector" in s.user_agent
    assert "ops@example.org" in s.user_agent
    assert "mailto" not in _settings(monkeypatch).user_agent


def test_json_log_line():
    record = logging.LogRecord("tc.x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    record.rows = 12
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "hello w"
    assert out["level"] == "INFO"
    assert out["rows"] == 12


def test_paris_date_and_hour_stamp():
    ts = datetime(2026, 11, 15, 23, 30, tzinfo=UTC)  # 00:30 on the 16th in Paris
    assert paris_date(ts) == date(2026, 11, 16)
    assert hour_stamp(ts) == "20261115T23Z"


def test_day_length_handles_dst():
    assert day_length_s(date(2026, 11, 16)) == 86400
    assert day_length_s(date(2026, 3, 29)) == 82800  # spring forward
    assert day_length_s(date(2026, 10, 25)) == 90000  # fall back


def test_ticker_skips_overruns_instead_of_bursting():
    now = [0.0]
    t = Ticker(120, monotonic=lambda: now[0])
    now[0] = 10.0
    assert t.next_wait() == 110.0  # normal: aligned on 120
    now[0] = 400.0  # long overrun: ticks at 240 and 360 missed, next is 480
    assert t.next_wait() == 80.0
    assert t.skipped == 2
