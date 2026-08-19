"""The Celery wiring for M4.

Thin and network-free, matching `test_profile_tasks.py`. The stage logic is covered
without a broker in `test_matching.py`; what is left is the wiring — the names resolve,
the schedule has an entry, and **both interlocks hold**.

The second interlock is the one worth having a test for. Part 14 says the match threshold
comes from the golden set, and every previous milestone has learned that a rule stated in
prose and not executed is a rule that gets quietly broken. This makes it mechanical: with
no threshold configured the matcher does nothing and records why.
"""

import importlib
import uuid
from typing import Any

import pytest
from db.models import Event, Match, Profile, User
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def celery_app(redis_url: str, migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MATCH_THRESHOLD", raising=False)

    import workers.app
    import workers.llm
    import workers.settings
    import workers.tasks.matching

    workers.settings.get_settings.cache_clear()
    # `llm.py` and `tasks/matching.py` both did `from workers.settings import
    # get_settings` at import, so each holds its own reference with its own cache.
    # Clearing only the module's copy makes this pass or fail on alphabetical ordering.
    workers.llm.get_settings.cache_clear()
    importlib.reload(workers.app)
    importlib.reload(workers.tasks.matching)
    return workers.app.app


def _reasons(session: Session, event_type: str) -> list[str]:
    events = session.scalars(select(Event).where(Event.type == event_type)).all()
    return [event.payload_json["reason"] for event in events]


def _profile(session: Session) -> Profile:
    user = User(email=f"{uuid.uuid4()}@example.com", auth_id=str(uuid.uuid4()))
    session.add(user)
    session.flush()
    profile = Profile(user_id=user.id, parsed_json={"basics": {}}, prefs_json={})
    session.add(profile)
    session.flush()
    session.commit()
    return profile


# ---- registration ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["workers.tasks.matching.match_all", "workers.tasks.matching.match_profile"]
)
def test_the_tasks_are_registered_under_their_dotted_names(celery_app: Any, name: str) -> None:
    """Beat reaches these by string. A module missing from TASK_MODULES leaves the
    worker answering `inspect ping` perfectly while rejecting every real task."""
    assert name in celery_app.tasks


def test_the_api_constant_matches_the_registered_name(celery_app: Any) -> None:
    """`POST /profiles/{id}/match` enqueues by this string, same as `PARSE_PROFILE` and
    `TAILOR_MATCH` — a rename that misses `api.queue.MATCH_PROFILE` leaves the manual
    trigger button queuing a message no worker will ever pick up."""
    from api.queue import MATCH_PROFILE

    assert MATCH_PROFILE == "workers.tasks.matching.match_profile"
    assert MATCH_PROFILE in celery_app.tasks


def test_matching_is_on_the_schedule(celery_app: Any) -> None:
    """Unlike parsing, this one is periodic: new jobs arrive on a clock, so matches do."""
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    assert "workers.tasks.matching.match_all" in scheduled
    # The fan-out is scheduled; the per-profile task is not, or every profile would be
    # scored once per profile per tick.
    assert "workers.tasks.matching.match_profile" not in scheduled


# ---- interlock 1: no key -----------------------------------------------------------


def test_matching_no_ops_without_an_api_key(celery_app: Any, session: Session) -> None:
    """The same shape as the aggregator without a proxy and the parse without a key."""
    import workers.tasks.matching as tasks

    assert tasks.match_all() == 0
    assert "no LLM API key configured" in _reasons(session, "match.skipped")


# ---- interlock 2: no threshold — Part 14, made mechanical --------------------------


def test_matching_no_ops_without_a_threshold(
    celery_app: Any, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Part 14 enforced rather than documented.**

    The match threshold is deferred until the golden set sets it empirically. With a key
    present and no threshold, the matcher must still refuse — otherwise the only thing
    standing between the pipeline and an invented number is someone remembering.
    """
    import workers.llm
    import workers.settings
    import workers.tasks.matching as tasks

    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    workers.settings.get_settings.cache_clear()
    workers.llm.get_settings.cache_clear()
    tasks.get_settings.cache_clear()

    assert tasks.match_all() == 0
    assert "no match threshold configured" in _reasons(session, "match.skipped")


def test_the_per_profile_task_checks_the_threshold_too(
    celery_app: Any, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not trusted from the dispatcher: this task is also reachable from `make match`
    and from a retry queued before the setting was cleared."""
    import workers.llm
    import workers.settings
    import workers.tasks.matching as tasks

    profile = _profile(session)
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    workers.settings.get_settings.cache_clear()
    workers.llm.get_settings.cache_clear()
    tasks.get_settings.cache_clear()

    assert tasks.match_profile(str(profile.id)) is None
    assert session.scalar(select(Match).where(Match.user_id == profile.user_id)) is None


def test_a_profile_whose_parse_failed_is_not_scored(
    celery_app: Any, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty `parsed_json` means the parse failed or never ran.

    Scoring it would compare every job against nothing and write confidently wrong
    matches — the exact worry DECISIONS.md recorded when M3 made a failed parse loud.
    """
    import workers.llm
    import workers.settings
    import workers.tasks.matching as tasks

    user = User(email=f"{uuid.uuid4()}@example.com", auth_id=str(uuid.uuid4()))
    session.add(user)
    session.flush()
    session.add(Profile(user_id=user.id, parsed_json={}, prefs_json={}))
    session.commit()

    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("MATCH_THRESHOLD", "60")
    workers.settings.get_settings.cache_clear()
    workers.llm.get_settings.cache_clear()
    tasks.get_settings.cache_clear()

    assert tasks.match_all() == 0
