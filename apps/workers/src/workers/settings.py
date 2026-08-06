"""Worker configuration.

Same rule as the API: no defaults on required values, and read through
`get_settings()` rather than built at import. `workers/app.py` calls it at module
scope, so a misconfigured worker still dies at start — but this module stays
importable without a complete environment.
"""

from functools import lru_cache

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: str = "local"
    database_url: PostgresDsn
    redis_url: RedisDsn

    # How often beat fires the ATS ingest pass. Six hours by default: boards change on
    # the order of a day, and Part 13 rule 12 forbids proxying this layer, so how often
    # we ask *is* the politeness budget. Overridable because the compose smoke has to
    # watch a tick actually happen, and it cannot wait six hours to do it.
    ingest_interval_minutes: int = 360


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
