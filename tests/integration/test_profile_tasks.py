"""The Celery wiring for M3.

Thin and network-free, matching test_scraping_tasks.py. The stage logic is covered
without a broker in test_profile_parse.py; the broker round trip itself in
test_celery_noop.py. What is left is the wiring: the name resolves, and the interlock
holds.
"""

import importlib
from typing import Any
from uuid import uuid4

import pytest
from db.models import Event
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def celery_app(redis_url: str, migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    import workers.app
    import workers.settings
    import workers.tasks.profiles

    workers.settings.get_settings.cache_clear()
    importlib.reload(workers.app)
    importlib.reload(workers.tasks.profiles)
    return workers.app.app


def test_the_task_is_registered_under_its_dotted_name(celery_app: Any) -> None:
    """The API enqueues by string, so a rename that misses `api.queue.PARSE_PROFILE`
    leaves every upload waiting on a task nobody will ever run."""
    from api.queue import PARSE_PROFILE

    assert PARSE_PROFILE == "workers.tasks.profiles.parse_profile"
    assert PARSE_PROFILE in celery_app.tasks


def test_parsing_has_no_beat_entry(celery_app: Any) -> None:
    """Deliberate. Parsing fires on upload, not on a clock — a schedule here would
    re-parse every profile forever, spending a model call each time to write the rows
    it already wrote."""
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    assert "workers.tasks.profiles.parse_profile" not in scheduled


def test_the_parse_no_ops_without_an_api_key(celery_app: Any, session: Session) -> None:
    """A safety interlock, not a guard clause — the same shape the aggregator uses
    without a proxy. §3.7's failure mode is a stage that quietly does nothing, so this
    also asserts it says so rather than returning in silence."""
    import workers.tasks.profiles as tasks

    assert tasks.parse_profile(str(uuid4())) is None

    skipped = session.scalars(
        select(Event).where(Event.type == "profile.parse_skipped").order_by(Event.id.desc())
    ).first()
    assert skipped is not None
    assert skipped.payload_json["reason"] == "no LLM API key configured"
