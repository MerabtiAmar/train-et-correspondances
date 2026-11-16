import pytest

SETTINGS_ENV = (
    "TRIP_UPDATES_URL", "TRIP_UPDATES_URL_FALLBACK", "ALERTS_URL", "STATIC_GTFS_URL",
    "POLL_SECONDS", "RUN_FOR_MINUTES", "RAW_SAMPLE_EVERY_MIN", "CONTACT_EMAIL", "MAX_LOCAL_MB",
    "HF_REPO", "HF_TOKEN", "DATA_DIR",
)  # fmt: skip


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """A developer's real HF_TOKEN or .env must never leak into (or be used by) a test."""
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
