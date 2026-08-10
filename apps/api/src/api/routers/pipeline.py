"""The dashboard's one purpose-built read: a user's matches, best first, jobs attached.

Everything else this API serves is `make_crud_router`'s generic list. That is right for a
table and wrong for a screen — `matches` has `job_id` and not the job's title, so the
naive version fetches every match and then every job one at a time.

Three queries, never N: the page of matches joined to their jobs, then the documents for
that page's match ids, then the keyword counts for the same ids. Reading the deduped pool
(§6.3) is enforced here rather than left to the caller, because "closed_at IS NULL AND
canonical_id IS NULL" is a fact about the schema and a dashboard should not have to know
it.

⚠️ Unauthenticated, like every route in this API. It takes a `user_id` from the path and
believes it. M8's problem; said out loud rather than discovered.
"""

from typing import Annotated
from uuid import UUID

from db.models import Document, Event, Job, Match
from fastapi import APIRouter, Query
from schemas.common import Page
from schemas.enums import MatchStatus
from schemas.pipeline import PipelineDocument, PipelineJob, PipelineRow
from sqlalchemy import func, select

from api.deps import SessionDep
from api.settings import get_settings

router = APIRouter(tags=["pipeline"])

# The event the tailoring stage writes its counters on. A string rather than an import:
# `apps/api` may not import `apps/workers` (§3.1), and the event type is the contract.
_TAILOR_GENERATED = "tailor.generated"


@router.get("/users/{user_id}/pipeline", response_model=Page[PipelineRow])
async def read_pipeline(
    user_id: UUID,
    session: SessionDep,
    status: MatchStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=get_settings().max_page_size)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PipelineRow]:
    live = (
        select(Match, Job)
        .join(Job, Job.id == Match.job_id)
        .where(Match.user_id == user_id)
        # The deduped pool, and the reason this is a join rather than two calls: a match
        # whose job was closed or collapsed into another row is history, not a candidate.
        .where(Job.closed_at.is_(None), Job.canonical_id.is_(None))
    )
    if status is not None:
        live = live.where(Match.status == status.value)

    total = await session.scalar(select(func.count()).select_from(live.subquery())) or 0

    rows = (
        await session.execute(
            # NULLS LAST because an unscored match is not a bad one — M4 leaves `score`
            # NULL when the posting stated no requirements, and sorting those to the top
            # would put "we could not judge this" above a genuine 82.
            live.order_by(Match.score.desc().nulls_last(), Match.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    match_ids = [match.id for match, _ in rows]
    documents = await _documents(session, match_ids)
    keywords = await _keywords(session, match_ids)

    return Page[PipelineRow](
        items=[
            PipelineRow(
                match_id=match.id,
                score=match.score,
                label=match.label,
                status=match.status,
                created_at=match.created_at,
                reasons_json=match.reasons_json,
                job=PipelineJob.model_validate(job),
                documents=documents.get(match.id, []),
                **keywords.get(match.id, {}),
            )
            for match, job in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _documents(
    session: SessionDep, match_ids: list[UUID]
) -> dict[UUID, list[PipelineDocument]]:
    """One query for the whole page. A match has at most two documents, so there is
    nothing to paginate and everything to gain from not asking per row."""
    if not match_ids:
        return {}

    found: dict[UUID, list[PipelineDocument]] = {}
    rows = await session.scalars(
        select(Document)
        .where(Document.match_id.in_(match_ids))
        .order_by(Document.type, Document.version.desc())
    )
    for row in rows:
        found.setdefault(row.match_id, []).append(PipelineDocument.model_validate(row))
    return found


async def _keywords(session: SessionDep, match_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    """The latest `tailor.generated` counters per match, if it has been tailored.

    Read from `events` rather than a column because these are a report and not state:
    §6.2 says changing a column's meaning is a new column plus a backfill, and a number
    that only exists to be looked at has not earned one. The stage already writes it here
    beside the bullet counts.
    """
    if not match_ids:
        return {}

    wanted = {str(value) for value in match_ids}
    rows = await session.scalars(
        select(Event)
        .where(Event.type == _TAILOR_GENERATED)
        .where(Event.payload_json["match_id"].astext.in_(wanted))
        # Ascending, so a re-tailor's later event overwrites the earlier one below.
        .order_by(Event.id)
    )

    found: dict[UUID, dict[str, int]] = {}
    for row in rows:
        payload = row.payload_json
        if "keywords_total" not in payload:
            # Written before the counters existed. Absent reads as "not measured", which
            # is the truth; zero would read as "measured, and nothing matched".
            continue
        found[UUID(payload["match_id"])] = {
            "keywords_matched": payload["keywords_matched"],
            "keywords_total": payload["keywords_total"],
        }
    return found
