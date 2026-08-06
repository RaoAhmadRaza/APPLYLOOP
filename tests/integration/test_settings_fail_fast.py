"""M0 gate item 8: secrets come from the environment, and their absence is loud.

A service that boots with a missing DATABASE_URL and only fails on the first request
that touches the database is the failure mode this prevents.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_missing_database_url_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.settings import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError) as exc:
        # _env_file=None so a developer's real .env can't mask the failure.
        Settings(_env_file=None)  # type: ignore[call-arg]

    missing = {e["loc"][0] for e in exc.value.errors()}
    assert {"database_url", "redis_url"} <= missing


def test_worker_settings_fail_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.settings import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_env_is_gitignored_but_the_template_is_not() -> None:
    """Part 13 rule 9. The `!.env.example` negation is easy to lose in a refactor and
    the consequence is a committed secret."""
    patterns = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in patterns
    assert "!.env.example" in patterns


def test_env_example_lists_every_required_key() -> None:
    """A template missing a key means a new developer's first run fails at boot with
    a ValidationError instead of working."""
    text = (REPO_ROOT / ".env.example").read_text()
    for key in ("DATABASE_URL", "REDIS_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert f"{key}=" in text, f"{key} missing from .env.example"


def test_env_example_holds_no_real_secret() -> None:
    text = (REPO_ROOT / ".env.example").read_text().lower()
    assert "change-me" in text or "example" in text
