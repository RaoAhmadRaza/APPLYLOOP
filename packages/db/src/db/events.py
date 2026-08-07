"""One row in `events`, for any stage.

§8.2 wants rows-per-source-per-run and §3.7 wants alerting on volume rather than only on
errors; `events` carries both without a new table per stage.

This lives in `packages/db` rather than in the stage that first needed it. §3.1 forbids
`apps/workers/*` stage packages from importing each other, so the alternative to sharing
it here is a second copy in every stage — which is exactly the copy-paste drift the
style guide rules out. It was `workers.scraping.ingest.record` until M3 needed one too.

The caller owns the payload shape. A board names its ats/slug, a feed names itself, a
profile names its id — there is nothing useful to standardise across them.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from db.models import Event


def record(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    user_id: uuid.UUID | None = None,
) -> None:
    """Append one event. Flushes, commits nothing — the caller owns the transaction.

    `user_id` is keyword-only and defaults to None because most events are not
    per-user: one source serves every user who matches against it. M3's profile events
    are the first that genuinely belong to someone.
    """
    session.add(Event(user_id=user_id, type=event_type, payload_json=payload))
    session.flush()
