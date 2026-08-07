"""Worker configuration.

Same rule as the API: no defaults on required values, and read through
`get_settings()` rather than built at import. `workers/app.py` calls it at module
scope, so a misconfigured worker still dies at start — but this module stays
importable without a complete environment.
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
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

    # --- Layer 3, the free feeds -------------------------------------------------
    # How long a paginated feed waits between pages. One second is not a throughput
    # decision — Himalayas 429s, and none of these cost us anything to be polite to.
    feed_page_delay_seconds: float = 1.0

    # The share of the previous pass's row count below which a feed closes nothing.
    # Half is generous on purpose: these listings genuinely fluctuate, and the guard
    # only has to catch a collapse, not a dip. See feed._volume_ok.
    feed_volume_floor: float = 0.5

    # Age at which a *paginated* feed's rows are closed. Absence can never close them —
    # a posting missing from the pages we asked for may be on a page we did not — so
    # time is the only signal left. Six weeks is longer than a live posting usually
    # lasts and shorter than the noise floor of "is this still open?".
    feed_stale_days: int = 45

    # --- §4.3's reverse-index, the half of the registry that grows itself ---------
    grow_interval_minutes: int = 60
    # Employers resolved per run. The worst case is batch x 6 requests split across six
    # different hosts, so 25 is ~25 requests each per hour — quieter than one human
    # loading one careers page.
    grow_batch: int = 25
    # Open roles an employer needs before it earns a six-endpoint probe. One posting
    # does not; a second one means they are actually hiring.
    grow_min_jobs: int = 2
    grow_delay_seconds: float = 1.0
    # How long an employer we could not resolve is left alone. Companies do adopt an
    # ATS, so a miss is not permanent — but re-probing hourly forever is six wasted
    # requests an hour, per company, indefinitely.
    grow_retry_days: int = 30

    # --- Layer 2, the aggregators. The ONLY proxy in this repo ---------------------
    # §7.4 and Part 13 rule 12: the ATS layer and the free feeds never see one. They
    # cost nothing to hit directly, and residential bandwidth is §8.1's swing factor.
    #
    # SecretStr because these carry `user:pass@`, and repr(get_settings()) reaches a log
    # line or a Sentry breadcrumb eventually (§3.7, Part 13 rule 9).
    #
    # Empty is a safety interlock, not a missing value: with no proxy the aggregate task
    # no-ops. Scraping LinkedIn or Indeed from a bare datacentre or a developer's home IP
    # burns that IP and inflates §8.2's block rate.
    jobspy_proxies: list[SecretStr] = Field(default_factory=list)

    # Longer than the ATS interval on purpose: every request here spends residential
    # bandwidth and carries block risk, and aggregator listings are staler by nature.
    aggregate_interval_minutes: int = 720

    @field_validator("jobspy_proxies", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """pydantic-settings parses a `list[...]` field from the environment as JSON, so
        a bare comma-separated string would raise at worker boot. Comma-separated is the
        form a proxy vendor hands you, so accept it."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
