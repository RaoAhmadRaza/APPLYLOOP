"""Celery entrypoints for M4.

Same split as `tasks/scraping.py` and `tasks/profiles.py`: this opens a session, calls
into `workers.matching`, and commits. Everything under test lives in that package as
plain functions, so the suite can seed a pool, run the stage and assert the delta with no
broker in the way (Part 10).

**Two interlocks, and the second one is Part 14 made mechanical.** Without a key the
stage no-ops, exactly like the aggregator without a proxy. Without a threshold it also
no-ops — because Part 14 says the match threshold is set empirically by the golden set,
and a default anywhere in this repo would be that invention wearing a config file. The
matcher physically cannot run on a number nobody measured.
"""

from dataclasses import asdict
from typing import Any
from uuid import UUID

import httpx
from db.events import record
from db.models import Profile
from sqlalchemy import select

from workers import llm
from workers.app import SessionLocal, app
from workers.matching import match
from workers.settings import get_settings


@app.task(name="workers.tasks.matching.match_all")
def match_all() -> int:
    """Fan out one `match_profile` per parsed profile. Holds no business logic itself."""
    settings = get_settings()

    if not llm.is_configured():
        _skip("no LLM API key configured")
        return 0
    if settings.match_threshold is None:
        # Part 14's open question, enforced rather than documented. `make verify-live-match`
        # prints the threshold the golden set supports; until someone pastes it into the
        # environment, this stage does nothing and says why.
        _skip("no match threshold configured")
        return 0

    with SessionLocal() as session:
        # A profile whose parse failed has an empty `parsed_json`, and scoring it would
        # produce confidently wrong matches against nothing — the exact worry recorded in
        # DECISIONS.md when M3 made a failed parse loud.
        profile_ids = list(
            session.scalars(select(Profile.id).where(Profile.parsed_json != {})).all()
        )

    for profile_id in profile_ids:
        match_profile.delay(str(profile_id))
    return len(profile_ids)


@app.task(
    name="workers.tasks.matching.match_profile",
    # Transport only. A model that answered nonsense twice has already had
    # `llm.MAX_ATTEMPTS` inside one call, and a retry here re-spends the whole shortlist.
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def match_profile(profile_id: str) -> dict[str, Any] | None:
    """Score one profile against the deduped pool."""
    settings = get_settings()
    if settings.match_threshold is None:
        # Checked again rather than trusted from the dispatcher: this task is also
        # reachable directly, from `make match` and from a retry queued before the
        # setting was cleared.
        _skip("no match threshold configured")
        return None

    with SessionLocal() as session:
        profile = session.get(Profile, UUID(profile_id))
        if profile is None:
            # Deleted between fan-out and execution. Not an error.
            return None

        result = match.match_profile(
            session,
            profile,
            threshold=settings.match_threshold,
            top_n=settings.match_top_n,
            company_cap=settings.match_per_company_cap,
        )
        session.commit()
        return asdict(result)


def _skip(reason: str) -> None:
    """§3.7: a stage that quietly does nothing is the failure mode. Say so in `events`."""
    with SessionLocal() as session:
        record(session, "match.skipped", {"reason": reason})
        session.commit()
