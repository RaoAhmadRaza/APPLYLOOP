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

    # --- The LLM. M3 is the first stage to call one; M4 and M5 follow --------------
    # An OpenAI-compatible base URL rather than a provider SDK, so §7.2's "keep the
    # interface swappable" is one env var rather than a rewrite. Defaults to OpenRouter
    # because §7.2 wants a cheap model for scoring and a strong one for tailoring, and
    # routing both off one key is the whole reason that product exists.
    llm_base_url: str = "https://openrouter.ai/api/v1"

    # SecretStr, and Optional. Empty is a safety interlock, not a missing value: with no
    # key the parse task records `profile.parse_skipped` and does nothing, exactly like
    # the aggregator without a proxy. CI stays green without buying credit.
    llm_api_key: SecretStr | None = None

    # Résumé extraction is structured output on a few thousand tokens, once per user —
    # the cheapest LLM call in the system. A cheap model with genuine `json_schema`
    # support is the whole requirement. Overridable per deployment.
    llm_model: str = "google/gemini-2.5-flash"

    # Generous: a long résumé is a lot of input, and a retry costs a whole call.
    #
    # Raised from 120 on 2026-08-09, after M5's first live gate run died on it 25 minutes
    # in. 120 was written for the cheap extraction model; a reasoning-class model asked
    # for structured output over a résumé and a posting routinely runs past it. A timeout
    # is a ceiling rather than a wait, so raising it costs nothing on a call that answers.
    llm_timeout_seconds: float = 300.0

    # --- M4: matching and scoring --------------------------------------------------
    # **Embeddings may live at a different provider from the chat model, and now do.**
    # DeepSeek serves no /embeddings route at all — 404 on every host, verified
    # 2026-08-10 — so "one base URL for everything" stopped being true the day the chat
    # provider changed. Empty means "wherever the chat model is", which is what every
    # single-provider deployment wants and what this repo did until now.
    embed_base_url: str = ""
    embed_api_key: SecretStr | None = None

    # 768 native dimensions, which is what `job_embeddings.embedding` is declared as.
    # This string is stored verbatim in `job_embeddings.model` (with the template version
    # appended, see `matching.embed`), so changing it is additive *within a width*: old
    # vectors stay, new ones land beside them. Changing the WIDTH is a migration, because
    # one column cannot hold two.
    embed_model: str = "google/gemini-embedding-001"

    # **The Part 14 interlock, made mechanical.** None is not a missing value — it is the
    # state before the golden set has spoken. With no threshold the matching task records
    # `match.skipped` and does nothing, the same shape as the aggregator without a proxy.
    # Part 14 forbids inventing this number, and a default here would be exactly that
    # invention wearing a config file. `make verify-live-match` prints the one to paste.
    match_threshold: int | None = None

    # Jobs per profile per run that reach the LLM. §3.5's illustrative funnel says 40.
    # This is the cost dial: everything above it is free, everything below is per-job
    # spend, and it is the only number in the ladder that buys precision with money.
    match_top_n: int = 40

    # No more than this many shortlisted jobs from one employer. Measured, not guessed:
    # one company was 55% of the open pool the day this was written, and without a cap
    # every match a user sees comes from that one board.
    match_per_company_cap: int = 5

    # Twelve hours. Postings move on the order of a day, and this interval *is* the
    # cadence of the LLM bill. Already-scored jobs are excluded by a WHERE clause, so a
    # run over an unchanged pool costs nothing.
    match_interval_minutes: int = 720

    # --- M5: tailoring and the fabrication validator ---------------------------------
    # §7.2: "Tailoring LLM — strong model, routed. This is the output the user's career
    # depends on. This is the one place to spend." A separate slug rather than reusing
    # `llm_model`, because scoring runs per job and tailoring runs per shortlisted match,
    # and one key through OpenRouter routes both.
    #
    # Verified against OpenRouter's live model list rather than remembered: $2/Mtok in,
    # $10/Mtok out, which is ~$0.03 per tailored application against §8.1's $0.50. This
    # is the *third* member of the coupled model/base-url set — see `.env.example`.
    tailor_model: str = "anthropic/claude-sonnet-5"

    # Above this share of untraceable bullets the document is blocked rather than shipped
    # short. 0.30 because a model that grounded two thirds of its output slipped, and one
    # that grounded a third has stopped grounding — and a résumé missing most of its
    # content is worse than none, because it looks finished.
    tailor_strip_ceiling: float = 0.30

    # A résumé with fewer than this many surviving bullets is not a document a user would
    # send, however true every line of it is. **Capped at what the vault can supply** —
    # see `validate.verdict`.
    #
    # Three, not six. Six was chosen before any real vault had been looked at, and M5's
    # first live gate blocked 19 of 20 honest pairs on it: `two_column.pdf` holds four
    # bullet claims and could never satisfy it, and `senior_backend.pdf` holds exactly
    # six, so the floor demanded every one of them while the résumé prompt tells the model
    # to prefer fewer and stronger. Three is the fewest an experience section can carry
    # and still be a document rather than a stub.
    tailor_min_bullets: int = 3

    # The cost dial. Every match tailored is a strong-model call, so an unattended fan-out
    # over a full shortlist is real money per tick — deliberately small until M7 owns the
    # schedule and the budget together.
    tailor_max_per_run: int = 10

    # Object storage is configured by `packages/storage`, not here: the API writes the
    # upload and the worker reads it back, so neither app can own those settings.

    @property
    def embed_url(self) -> str:
        """Where `POST /embeddings` goes. Empty `EMBED_BASE_URL` means the chat provider.

        A property rather than an `or` at each call site because there are two consumers —
        the client factory and `model_slug_mismatch` — and if they ever disagreed about
        which host embeddings use, the check would validate the wrong one. That is the
        exact failure `model_slug_mismatch` exists to prevent, so it must not be able to
        cause it.
        """
        return self.embed_base_url or self.llm_base_url

    @property
    def embed_key(self) -> SecretStr | None:
        """Falls back deliberately, and not gated on `embed_base_url` being empty.

        Sending the chat key to a foreign embeddings host earns a legible 401. Refusing to
        fall back would instead break the legitimate case of pointing `EMBED_BASE_URL` at a
        host the existing key already works for.
        """
        return self.embed_api_key or self.llm_api_key

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


def model_slug_mismatch(settings: "Settings") -> str | None:
    """Name any model slug that cannot exist at the configured base URL, or `None`.

    Three model settings and one base URL are a coupled set: an OpenRouter slug carries a
    vendor prefix (`openai/gpt-5`) and OpenAI's own API takes the bare name (`gpt-5`).
    Cross them and the provider answers 400 naming a model it has never heard of, which
    reads like an outage rather than a typo.

    **A check rather than a comment, because the comment did not work.** The warning above
    `EMBED_MODEL` in `.env.example` was written after this mismatch cost a live M4 gate
    run. M5's first live run hit it again within the hour, on the variable whose warning
    had just been extended to cover it.

    A function the interlock calls rather than a validator on `Settings`, deliberately.
    Refusing to construct `Settings` makes two independent settings inseparable — every
    test that pins a fake model slug then has to pin a provider for it too — and this
    repo's shape for "configured wrong" is a stage that records why and does nothing, not
    a process that will not boot.

    Only the two providers configured here are checked. A third OpenAI-compatible host
    may use slashes or not, and guessing on its behalf turns a helpful check into a false
    refusal, which is the worse failure. That escape hatch is what lets embeddings sit on
    Gemini without this function inventing a rule for a host it has never seen.

    **Two hosts, since 2026-08-10.** `EMBED_MODEL` is judged against the embeddings host
    and the other two against the chat host. Judging all three against one URL was correct
    only while one URL served both, and it silently became wrong the day it did not.
    """
    problems = [
        problem
        for problem in (
            _slugs_at(
                settings.llm_base_url,
                "LLM_BASE_URL",
                {"LLM_MODEL": settings.llm_model, "TAILOR_MODEL": settings.tailor_model},
            ),
            _slugs_at(
                settings.embed_url,
                "EMBED_BASE_URL" if settings.embed_base_url else "LLM_BASE_URL",
                {"EMBED_MODEL": settings.embed_model},
            ),
        )
        if problem
    ]
    # Joined, never `or`: the two groups are checked against different hosts now, and
    # returning the first would hide EMBED_MODEL behind LLM_MODEL — reintroducing the
    # fix-one-hit-the-next-next-run defect this function was written against. When
    # embeddings fall back, the variable named is the chat one, because that is the line
    # the operator has to edit.
    return "; ".join(problems) or None


def _slugs_at(base_url: str, variable: str, slugs: dict[str, str]) -> str | None:
    """The per-host rule, unchanged — now applied to one group of slugs at a time."""
    if "openrouter.ai" in base_url:
        wrong = [name for name, slug in slugs.items() if "/" not in slug]
        expectation = "OpenRouter slugs carry a vendor prefix, e.g. openai/gpt-5"
    elif "api.openai.com" in base_url:
        wrong = [name for name, slug in slugs.items() if "/" in slug]
        expectation = "OpenAI's own API takes the bare model name, e.g. gpt-5"
    else:
        return None

    if not wrong:
        return None
    return f"{', '.join(wrong)} cannot exist at {variable}={base_url} — {expectation}"
