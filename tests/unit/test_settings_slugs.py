"""The model slugs and the base URL are a coupled set, and now something checks it.

Written the hour M5's first live run 400'd on `TAILOR_MODEL=anthropic/claude-sonnet-5`
against `LLM_BASE_URL=https://api.openai.com/v1` — a `.env` pointing at OpenAI direct and
a default slug written for OpenRouter. The identical mismatch cost a live M4 gate run on
`EMBED_MODEL`, and the fix that time was a warning comment in `.env.example`. The comment
had just been extended to cover the third variable when the third variable broke.

A pure function over a constructed `Settings`, rather than a validator that refuses to
build one. The first attempt *was* a validator, and it made two independent settings
inseparable: every test that pins a fake model slug then has to pin a provider for it
too. It also turned a configuration mistake into a process that will not boot, where this
repo's shape for that is a stage which records why and does nothing.
"""

from workers.settings import Settings, model_slug_mismatch

BASE = {
    "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
}


def _settings(**overrides: str) -> Settings:
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


def test_an_openrouter_url_with_a_bare_slug_is_named() -> None:
    mismatch = model_slug_mismatch(
        _settings(llm_base_url="https://openrouter.ai/api/v1", tailor_model="claude-sonnet-5")
    )

    assert mismatch is not None
    assert "TAILOR_MODEL" in mismatch


def test_an_openai_url_with_a_prefixed_slug_is_named() -> None:
    """The exact configuration that broke, in the exact direction it broke."""
    mismatch = model_slug_mismatch(
        _settings(
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-5.4-nano",
            embed_model="text-embedding-3-small",
            tailor_model="anthropic/claude-sonnet-5",
        )
    )

    assert mismatch is not None
    assert "TAILOR_MODEL" in mismatch
    assert "bare model name" in mismatch


def test_every_mismatched_variable_is_named_at_once() -> None:
    """Naming one of three means fixing it and hitting the next on the following run."""
    mismatch = model_slug_mismatch(
        _settings(
            llm_base_url="https://api.openai.com/v1",
            llm_model="openai/gpt-5",
            embed_model="openai/text-embedding-3-small",
            tailor_model="anthropic/claude-sonnet-5",
        )
    )

    assert mismatch is not None
    assert "LLM_MODEL" in mismatch
    assert "EMBED_MODEL" in mismatch
    assert "TAILOR_MODEL" in mismatch


def test_a_consistent_set_reports_nothing() -> None:
    assert (
        model_slug_mismatch(
            _settings(
                llm_base_url="https://openrouter.ai/api/v1",
                llm_model="google/gemini-2.5-flash",
                embed_model="openai/text-embedding-3-small",
                tailor_model="anthropic/claude-sonnet-5",
            )
        )
        is None
    )
    assert (
        model_slug_mismatch(
            _settings(
                llm_base_url="https://api.openai.com/v1",
                llm_model="gpt-5.4-nano",
                embed_model="text-embedding-3-small",
                tailor_model="gpt-5",
            )
        )
        is None
    )


def test_an_unrecognised_host_is_left_alone() -> None:
    """A third OpenAI-compatible provider may use slashes or not. Guessing on its behalf
    turns a helpful check into a false refusal, which is the worse failure."""
    assert (
        model_slug_mismatch(
            _settings(
                llm_base_url="https://api.together.xyz/v1",
                tailor_model="meta-llama/Llama-4-70b",
            )
        )
        is None
    )


def test_the_shipped_defaults_agree_with_each_other() -> None:
    """The defaults are a set someone will run unchanged, and the mistake this catches is
    exactly the one where one of the three moved and the others did not."""
    assert model_slug_mismatch(_settings()) is None
