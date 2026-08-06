"""API configuration.

`settings` is instantiated at import time on purpose: a missing DATABASE_URL should
kill the container at boot with a clear ValidationError, not surface as a 500 on the
first request that happens to touch the database.

Required fields carry no default. pydantic-settings validates defaults too, so a
placeholder default would pass validation and then fail at connect time — which is
exactly the failure mode this is meant to prevent.
"""

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: str = "local"
    project_name: str = "APPLYLOOP"

    # No defaults — see the module docstring.
    database_url: PostgresDsn
    redis_url: RedisDsn

    # Bounds every collection endpoint. An unbounded list over `jobs` becomes a
    # footgun the moment M1 lands 100k rows.
    max_page_size: int = 100


settings = Settings()  # type: ignore[call-arg]
