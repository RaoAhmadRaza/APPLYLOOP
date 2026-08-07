"""One board in, a row delta out. This is M1's gate item 2.

Deliberately a plain function over `(session, client, company)` rather than a Celery
task: Part 10's rule is "seed the tables the stage reads, run it, assert the tables it
writes", and a task would put a broker between the test and the assertion.

Change detection is a payload diff, not a timestamp comparison. Of the six providers
only Greenhouse exposes an `updated_at` — Lever, Ashby, Workable, SmartRecruiters and
Recruitee expose creation dates only. So the question "did this posting change?" is
answered by comparing payloads, and Postgres answers it for free: `jsonb` equality is
semantic and key-order-insensitive, so no `content_hash` column is needed.

`upsert`, `close_missing` and `record` are public because M2's feed layer (`feed.py`)
and aggregator layer (`aggregator.py`) write rows through exactly these. They take a
`company_id` rather than a `Company` for the same reason: a feed row names an employer
we may hold no registry row for, so there is nothing to pass. One diff engine, one
close statement, one event shape, three sources.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from db.models import Company, Event, Job
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from workers.scraping import ADAPTERS

# Consecutive failed runs before a slug is retired. Five rather than one so a single
# 503 never retires a live board; §4.3 asks for "N runs" without naming N.
RETIRE_AFTER_FAILURES = 5

# Rows per INSERT statement. `insert(Job).values(rows)` renders one multi-VALUES
# statement and Postgres caps a statement at 65,535 bind parameters; JobCreate has 13
# fields, so a single statement dies just above 5,041 rows. SmartRecruiters already
# allows 10,000 postings from one board, and M2's feeds return four figures.
UPSERT_CHUNK = 500

# Columns the upsert refreshes. `source`/`external_id` are the conflict key and never
# change; `created_at` records first sight and must survive an update.
_MUTABLE = (
    "title",
    "company",
    "company_id",
    "location",
    "locations",
    "remote_mode",
    "description",
    "url",
    "ats_type",
    "posted_at",
    "raw_json",
)


@dataclass(frozen=True)
class IngestResult:
    fetched: int
    inserted: int
    updated: int
    closed: int

    @property
    def changed(self) -> int:
        return self.inserted + self.updated


def ingest_company(session: Session, client: httpx.Client, company: Company) -> IngestResult:
    """Fetch one board, upsert the diff, close what vanished. Commits nothing."""
    adapter = ADAPTERS[AtsType(company.ats_type)]
    slug = company.ats_slug

    postings = adapter.fetch(client, slug)
    if not postings:
        # §3.7's most common real failure: a board that changed its markup returns zero
        # rows and raises nothing. Closing here would delete this employer's entire job
        # set on one transient glitch, so the run records itself and stops. A genuinely
        # empty board leaves stale rows open — visible in the event log, not destructive.
        _record_board(session, company, "ingest.empty", {"fetched": 0})
        return IngestResult(fetched=0, inserted=0, updated=0, closed=0)

    jobs = [adapter.normalize(raw, company.name, slug) for raw in postings]
    changed_ids, inserted, updated = upsert(session, jobs, company.id)
    _fetch_missing_details(session, client, adapter, company, changed_ids)
    closed = close_missing(session, adapter.SOURCE, [job.external_id for job in jobs], company.id)

    return IngestResult(fetched=len(jobs), inserted=inserted, updated=updated, closed=closed)


def upsert(
    session: Session, jobs: Sequence[Any], company_id: uuid.UUID | None
) -> tuple[list[str], int, int]:
    """Insert new postings, update only genuinely changed ones.

    Returns (changed external_ids, inserted count, updated count).

    `company_id` is required rather than defaulted: `None` means "this row names an
    employer with no registry entry", which is right for a feed and wrong for a board,
    and nobody should reach it by omission.
    """
    # Postgres refuses an ON CONFLICT DO UPDATE that would touch one row twice in a
    # single statement ("cannot affect row a second time"), so a source that returns the
    # same posting twice in one pass would raise rather than dedupe. An ATS board never
    # does; a feed paginated by offset does it routinely, because a posting inserted at
    # the top of the listing between two page requests shifts everything down one. Last
    # occurrence wins: for a shifted page that is the more recently fetched copy.
    deduped = list({job.external_id: job for job in jobs}.values())

    changed_ids: list[str] = []
    inserted = 0
    updated = 0
    # Chunked because one statement has a bind-parameter ceiling — see UPSERT_CHUNK.
    for start in range(0, len(deduped), UPSERT_CHUNK):
        ids, new, touched = _upsert_chunk(
            session, deduped[start : start + UPSERT_CHUNK], company_id
        )
        changed_ids.extend(ids)
        inserted += new
        updated += touched
    return changed_ids, inserted, updated


def _upsert_chunk(
    session: Session, jobs: Sequence[Any], company_id: uuid.UUID | None
) -> tuple[list[str], int, int]:
    rows = [{**job.model_dump(), "company_id": company_id} for job in jobs]

    insertion = insert(Job).values(rows)
    statement = insertion.on_conflict_do_update(
        index_elements=[Job.source, Job.external_id],
        set_={
            **{column: insertion.excluded[column] for column in _MUTABLE},
            # The Timestamps mixin's onupdate is Python-side only, so a Core upsert
            # would otherwise leave updated_at frozen at insert time — and "which rows
            # did this run touch?" is exactly what gate item 2 asks.
            "updated_at": func.now(),
            # Seeing a posting again means it is listed again.
            "closed_at": None,
            # A changed payload can change title, company or location, so the derived
            # key is stale. Clearing it hands recomputation to the dedupe pass, which
            # keeps one owner of the normalizer. This sits inside `set_`, so it fires
            # only when the `where` below has already decided the row genuinely changed
            # — `_MUTABLE` and the diff invariant are both untouched.
            "dedupe_key": None,
        },
        # The whole of "writes only diffs". Without it every run rewrites every row.
        # The closed_at arm is load-bearing on its own: a job that was closed and then
        # reposted with a byte-identical payload must still reopen.
        where=or_(
            Job.raw_json != insertion.excluded.raw_json,
            Job.closed_at.is_not(None),
        ),
    ).returning(
        Job.external_id,
        # Postgres system column: zero on a fresh tuple, non-zero on one an UPDATE
        # replaced. Cheapest way to tell insert from update in a single round trip.
        (literal_column("xmax") == 0).label("inserted"),
    )

    # Rows filtered out by the WHERE are not returned at all, so this result set IS the
    # changed set — nothing further to compare.
    changed = session.execute(statement).all()

    inserted = sum(1 for row in changed if row.inserted)
    return [row.external_id for row in changed], inserted, len(changed) - inserted


def _fetch_missing_details(
    session: Session,
    client: httpx.Client,
    adapter: Any,
    company: Company,
    changed_external_ids: list[str],
) -> None:
    """SmartRecruiters withholds descriptions from its list response.

    Only postings the upsert actually changed pay the extra request, so a steady-state
    run makes none at all. A board's first sync still pays one per posting — inherent
    to their API, which offers no bulk detail endpoint.
    ponytail: N calls on first sync; batch it only if a real board makes that hurt.

    Why *changed* rather than only *inserted*: the listing has no description, so an
    edited posting would otherwise have its description overwritten with NULL by the
    upsert and never refilled.

    The detail payload is deliberately NOT merged back into `raw_json`. That column is
    the thing the next run diffs against, and it must keep holding exactly what the list
    endpoint returns — otherwise every run finds a difference, rewrites every row, and
    "writes only diffs" quietly stops being true.
    """
    fetch_detail = getattr(adapter, "fetch_detail", None)
    if fetch_detail is None or not changed_external_ids:
        return

    prefix = f"{company.ats_slug}:"
    for external_id in changed_external_ids:
        detail = fetch_detail(client, company.ats_slug, external_id.removeprefix(prefix))
        if detail is None:
            continue
        job = session.scalars(
            select(Job).where(Job.source == adapter.SOURCE, Job.external_id == external_id)
        ).one()
        enriched = adapter.normalize({**job.raw_json, **detail}, company.name, company.ats_slug)
        job.description = enriched.description
        job.url = enriched.url
    session.flush()


def close_missing(
    session: Session,
    source: str,
    seen_external_ids: list[str],
    company_id: uuid.UUID | None,
) -> int:
    """Mark every still-open posting this source stopped returning.

    Scoped by company_id when there is one, not by the raw `company` text: two boards
    spelling a name differently would otherwise close each other's rows. A feed passes
    `None` and is scoped by `source` alone, which is safe because the two namespaces are
    disjoint — no board row is ever `source='remotive'` and no feed row is ever
    `source='greenhouse'`.

    Callers decide *whether* to call this at all. An ATS board returns the complete
    current set for one employer, so absence is evidence; a paginated feed or an
    aggregator search proves nothing by absence. See `feed.ingest_feed`.
    """
    scope = [Job.company_id == company_id] if company_id is not None else []
    closed = session.scalars(
        update(Job)
        .where(
            *scope,
            Job.source == source,
            Job.closed_at.is_(None),
            Job.external_id.not_in(seen_external_ids),
        )
        .values(closed_at=func.now(), updated_at=func.now())
        .returning(Job.id)
    ).all()
    session.flush()
    return len(closed)


def record_success(session: Session, company: Company, result: IngestResult) -> None:
    """Mark the board healthy and log the run's row counts.

    §8.2 wants rows-ingested-per-source-per-run, and §3.7 wants alerting on volume
    rather than only on errors. `events` already carries both without a new table.
    """
    now = datetime.now(UTC)
    company.last_seen_ok = now
    company.jobs_last_run = now
    company.consecutive_failures = 0
    _record_board(
        session,
        company,
        "ingest.run",
        {
            "fetched": result.fetched,
            "inserted": result.inserted,
            "updated": result.updated,
            "closed": result.closed,
        },
    )


def record_failure(session: Session, company: Company, error: str) -> None:
    """Count the failure and retire the slug once it stops being a blip."""
    company.consecutive_failures += 1
    if company.consecutive_failures >= RETIRE_AFTER_FAILURES:
        company.status = CompanyStatus.RETIRED.value
    _record_board(
        session,
        company,
        "ingest.error",
        {"error": error[:500], "consecutive_failures": company.consecutive_failures},
    )


def record(session: Session, event_type: str, payload: dict[str, Any]) -> None:
    """One row in `events`. §8.2 wants rows-ingested-per-source-per-run and §3.7 wants
    alerting on volume rather than only on errors; `events` carries both without a new
    table. The caller owns the payload shape — a board names its ats/slug, a feed names
    itself."""
    session.add(
        Event(
            # Ingest is not per-user: one source serves every user who matches against it.
            user_id=None,
            type=event_type,
            payload_json=payload,
        )
    )
    session.flush()


def _record_board(
    session: Session, company: Company, event_type: str, payload: dict[str, Any]
) -> None:
    record(session, event_type, {"ats": company.ats_type, "slug": company.ats_slug, **payload})
