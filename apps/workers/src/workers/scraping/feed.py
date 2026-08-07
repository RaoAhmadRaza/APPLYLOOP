"""One free feed in, a row delta out. Layer 3's counterpart to `ingest.ingest_company`.

A sibling rather than a generalisation of `ingest_company`, because almost everything
that function branches on is board-specific — `fetch_detail`, `consecutive_failures`,
retirement, the ats/slug event payload. Sharing it would mean `if company: ... else:`
throughout, and `ingest.py` is already past the size this repo prefers. What the two do
share is the part worth sharing: `ingest.upsert` and `ingest.close_missing`, one diff
engine between them, plus `db.events.record`.

The one destructive statement in this stage is the close, and it is guarded three ways.
M1 can close every posting a board stopped returning because an ATS board returns the
complete current set for one employer. That is not true of a feed:

  * `COMPLETE = False` (paginated) — a posting missing from the pages we asked for may
    simply be on a page we did not ask for. Never closed by absence; aged out instead.
  * an empty pass closes nothing. §3.7's most common real failure is a source that
    returns zero rows and raises nothing.
  * a pass that collapses against the previous one closes nothing and says so. A feed
    that returns 40 postings where it returned 1,400 yesterday is broken, not empty,
    and closing on it would retire the whole feed's history in one tick.

That last guard is why §3.7's "alert on volume, not just errors" is implemented here as
a gate rather than as a dashboard: by the time a human reads the dashboard the rows are
already closed.
"""

from types import ModuleType
from typing import Any

import httpx
from db.events import record
from db.models import Event, Job
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from workers.scraping import ingest
from workers.scraping.ingest import IngestResult


def ingest_feed(
    session: Session,
    client: httpx.Client,
    feed: ModuleType,
    *,
    pages: int | None = None,
    delay: float = 0.0,
    volume_floor: float = 0.5,
) -> IngestResult:
    """Fetch one feed, upsert the diff, close what safely can be. Commits nothing."""
    postings = feed.fetch(client, pages if pages is not None else feed.PAGES, delay)
    if not postings:
        record(session, "feed.empty", {"feed": feed.SOURCE, "fetched": 0})
        return IngestResult(fetched=0, inserted=0, updated=0, closed=0)

    jobs = [feed.normalize(raw) for raw in postings]
    # No company_id: a feed names an employer we may hold no registry row for. `grow`
    # fills these in later, and `dedupe` keys on the company text either way.
    _, inserted, updated = ingest.upsert(session, jobs, None)

    closed = 0
    if feed.COMPLETE and _volume_ok(session, feed.SOURCE, len(jobs), volume_floor):
        closed = ingest.close_missing(session, feed.SOURCE, [job.external_id for job in jobs], None)

    record(
        session,
        "feed.run",
        {
            "feed": feed.SOURCE,
            "fetched": len(jobs),
            "inserted": inserted,
            "updated": updated,
            "closed": closed,
        },
    )
    return IngestResult(fetched=len(jobs), inserted=inserted, updated=updated, closed=closed)


def close_stale(session: Session, source: str, older_than_days: int) -> int:
    """Age out a paginated feed's rows, since absence can never close them.

    COALESCE(posted_at, created_at): a feed that omits a date still has a first-seen row
    timestamp, and a NULL date must not exempt a posting from ever being closed.

    Deliberately not applied to a COMPLETE feed. Remotive still listing a sixty-day-old
    role is Remotive telling us it is alive, and an age rule would overrule the source.
    """
    closed = session.scalars(
        update(Job)
        .where(
            Job.source == source,
            Job.closed_at.is_(None),
            func.coalesce(Job.posted_at, Job.created_at)
            < func.now() - text(f"make_interval(days => {int(older_than_days)})"),
        )
        .values(closed_at=func.now(), updated_at=func.now())
        .returning(Job.id)
    ).all()
    session.flush()
    return len(closed)


def _volume_ok(session: Session, source: str, fetched: int, floor: float) -> bool:
    """Did this pass return a believable share of what the last one did?

    Reads the previous count back out of `events` rather than adding a column: the run
    event is already written for §8.2, and this is the same number.
    """
    previous = _previous_fetched(session, source)
    if previous is None or fetched >= previous * floor:
        return True

    record(
        session,
        "feed.volume_drop",
        {"feed": source, "fetched": fetched, "previous": previous, "floor": floor},
    )
    return False


def _previous_fetched(session: Session, source: str) -> int | None:
    statement = (
        select(Event.payload_json["fetched"].as_integer())
        .where(
            Event.type == "feed.run",
            Event.payload_json["feed"].astext == source,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    result: Any = session.scalar(statement)
    return int(result) if result is not None else None
