"""Celery entrypoint for M3.

Same split as `tasks/scraping.py`: this opens a session, calls into `workers.profiles`,
and commits. Everything under test lives in that package as plain functions, so the
integration suite can seed a profile, run the stage and assert the delta with no broker
in the way (Part 10).

No beat entry, deliberately. Parsing is event-driven — it happens when someone uploads a
résumé — so there is nothing for a schedule to do. M3 adds no cron.
"""

from dataclasses import asdict
from typing import Any
from uuid import UUID

from db.events import record
from db.models import Profile
from storage import StorageError

from workers import llm
from workers.app import SessionLocal, app
from workers.profiles import extract, parse


@app.task(
    name="workers.tasks.profiles.parse_profile",
    # An extraction failure is deterministic — a scanned PDF will not become readable on
    # the third try — so only the transport-shaped failures retry. A model that answered
    # nonsense twice has already had `llm.MAX_ATTEMPTS` inside one call.
    autoretry_for=(StorageError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def parse_profile(profile_id: str) -> dict[str, Any] | None:
    """Parse one profile's résumé into `parsed_json`, the promoted columns, and a vault."""
    if not llm.is_configured():
        # A safety interlock, not a guard clause — the same shape as the aggregator
        # without a proxy. §3.7's failure mode is a stage that quietly does nothing, so
        # this says so in `events` rather than returning silently.
        with SessionLocal() as session:
            record(session, "profile.parse_skipped", {"reason": "no LLM API key configured"})
            session.commit()
        return None

    with SessionLocal() as session:
        profile = session.get(Profile, UUID(profile_id))
        if profile is None:
            # Deleted between the upload and this task running. Not an error.
            return None

        try:
            result = parse.parse_profile(session, profile)
        except (extract.ExtractError, llm.LlmError, parse.ParseEmptyError) as error:
            # Loud, and on the row's own audit trail. A profile whose parse failed must
            # not look like a profile with an empty résumé — M4 would score it against
            # nothing and produce confidently wrong matches.
            record(
                session,
                "profile.parse_failed",
                {"error": str(error)[:500]},
                user_id=profile.user_id,
            )
            session.commit()
            raise

        session.commit()
        return asdict(result)
