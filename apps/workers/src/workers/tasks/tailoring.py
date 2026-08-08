"""Celery entrypoints for M5.

Same split as every other stage: this opens a session, calls into `workers.tailoring`,
and commits. The logic under test lives in that package as plain functions, so the suite
seeds a match, runs the stage and asserts the delta with no broker in the way (Part 10).

**Two interlocks, and the second one is stricter than M3's.** Without an LLM key the
stage no-ops, like every other model-using stage. Without object storage it *also*
no-ops — where M3's upload path merely degrades to a 503, a generated document that
cannot be stored is not a document, and rendering into nowhere would spend the strong
model to produce bytes nobody can reach.

**No beat entry, deliberately.** A run of `match_all` leaves tens of `discovered`
matches, and each one tailored is a strong-model call: an unattended tick is real money.
M7 owns scheduling and will add it with the cost budget in hand. Until then `make tailor`
drives it, and `tailor_all` exists so that wiring is one line rather than a rewrite.

**No `autoretry_for`, also deliberately.** Every other stage retries transport errors,
but here the expensive, non-repeatable part — two strong-model calls — happens before
anything that can fail transiently, and a retry re-spends it. A failure leaves the match
`discovered`, which is precisely the state the next run selects on. The state machine is
the retry.
"""

from dataclasses import asdict
from typing import Any

import storage
from db.events import record
from db.models import Match
from schemas.enums import MatchStatus
from sqlalchemy import select

from workers import llm
from workers.app import SessionLocal, app
from workers.settings import get_settings, model_slug_mismatch
from workers.tailoring import render, tailor


@app.task(name="workers.tasks.tailoring.tailor_all")
def tailor_all() -> int:
    """Fan out one `tailor_match` per `discovered` match, capped. Holds no logic itself."""
    settings = get_settings()

    if not llm.is_configured():
        _skip("no LLM API key configured")
        return 0
    if not storage.is_configured():
        _skip("no object storage configured")
        return 0
    mismatch = model_slug_mismatch(settings)
    if mismatch:
        # Caught before the first call rather than as a 400 from the provider. M5's own
        # first live run spent nothing and learned nothing from that 400 — it names a
        # model the provider has never heard of, which reads like an outage.
        _skip(mismatch)
        return 0

    with SessionLocal() as session:
        match_ids = list(
            session.scalars(
                select(Match.id)
                .where(Match.status == MatchStatus.DISCOVERED.value)
                # Oldest first: a match that has waited through a run should not keep
                # losing to whatever the scorer produced most recently.
                .order_by(Match.created_at)
                .limit(settings.tailor_max_per_run)
            ).all()
        )

    for match_id in match_ids:
        tailor_match.delay(str(match_id))
    return len(match_ids)


@app.task(name="workers.tasks.tailoring.tailor_match")
def tailor_match(match_id: str) -> dict[str, Any] | None:
    """Tailor one match into two stored documents, or block it loudly."""
    settings = get_settings()
    if not llm.is_configured() or not storage.is_configured():
        # Re-checked rather than trusted from the dispatcher: this task is reachable
        # directly from `make tailor`, and from a message queued before a setting moved.
        _skip("no LLM API key or object storage configured")
        return None
    mismatch = model_slug_mismatch(settings)
    if mismatch:
        _skip(mismatch)
        return None

    with SessionLocal() as session:
        try:
            result = tailor.tailor_match(
                session,
                match_id,
                model=settings.tailor_model,
                strip_ceiling=settings.tailor_strip_ceiling,
                min_bullets=settings.tailor_min_bullets,
            )
        except (llm.LlmError, render.RenderError, storage.StorageError) as error:
            # A failed tailor must not be indistinguishable from a match nobody reached
            # yet — M3's lesson, one milestone on. The match stays `discovered`, so the
            # next run tries again; this row is the only evidence it was ever attempted.
            record(
                session,
                "tailor.failed",
                {"match_id": match_id, "error": str(error)[:500]},
            )
            session.commit()
            raise

        session.commit()
        return asdict(result) if result is not None else None


def _skip(reason: str) -> None:
    """§3.7: a stage that quietly does nothing is the failure mode. Say so in `events`."""
    with SessionLocal() as session:
        record(session, "tailor.skipped", {"reason": reason})
        session.commit()
