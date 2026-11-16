"""Settings: every value comes from the environment (or .env) and has a default."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_NAME = "train-correspondances-collector"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Feeds
    trip_updates_url: str = (
        "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
    )
    trip_updates_url_fallback: str = (
        "https://proxy.transport.data.gouv.fr/resource/sncf-all-gtfs-rt-trip-updates"
    )
    alerts_url: str = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts"
    static_gtfs_url: str = (
        "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"
    )

    # Polling
    poll_seconds: int = 120
    run_for_minutes: int = 0  # 0 = forever
    raw_sample_every_min: int = 60
    flush_every_min: int = 10
    stale_after_min: int = 10  # feed header older than this = "stale"

    # HTTP
    http_timeout_s: float = 20.0
    http_retries: int = 3
    http_backoff_s: float = 1.0
    contact_email: str = ""

    # Storage
    data_dir: Path = Path("data")
    max_local_mb: int = 2048
    upload_after_hour: int = 3  # Europe/Paris
    static_check_after_hour: int = 4  # Europe/Paris, once per day

    # Hugging Face (empty token or repo = dry-run)
    hf_repo: str = ""
    hf_token: SecretStr | None = None

    # Health
    health_max_age_s: int = 600

    @field_validator("hf_token", mode="before")
    @classmethod
    def _blank_token_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def user_agent(self) -> str:
        ua = f"{PROJECT_NAME}/0.1"
        return f"{ua} (+mailto:{self.contact_email})" if self.contact_email else ua

    @property
    def dry_run(self) -> bool:
        return self.hf_token is None or not self.hf_repo.strip()

    @property
    def state_dir(self) -> Path:
        return self.data_dir / ".state"

    @property
    def heartbeat_path(self) -> Path:
        return self.state_dir / "heartbeat.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
