"""API configuration.

Required fields carry no default. pydantic-settings validates defaults too, so a
placeholder default would pass validation and then fail at connect time — which is
exactly the failure mode this is meant to prevent.

Settings are read through `get_settings()`, not built at import time. `main.py` calls
it while constructing the app at module scope, so a misconfigured container still dies
at boot rather than on the first request — but importing this module no longer
*requires* a complete environment. Import-time instantiation made the module
unimportable in any context that wanted to build its own Settings, which is exactly
what the test suite does.
"""

from functools import lru_cache

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

    # M8's dashboard (`apps/web`) is a browser app on its own origin, so every fetch
    # it makes is a CORS request. The extension is exempt (host_permissions bypasses
    # CORS for extension-context fetches), so this is only for the dashboard.
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
