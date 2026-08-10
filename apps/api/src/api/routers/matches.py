"""Actions on a match: approve, skip, tailor.

**Not `PATCH /matches/{id}` with a status in the body.** §6.1 defines a state machine and
says nothing may skip a transition; the generic CRUD router will happily write any value
the CHECK constraint allows, in any order. A named endpoint is where that rule can live,
and the rule is the reason these exist at all.

**Every one of them is idempotent**, because §3.4 says a double-tap is expected rather
than exceptional — a dashboard button pressed twice, a retried request, a user going back.
The transitions are conditional UPDATEs and the row is the lock: nothing here reads a
status and then decides what to write, which is Part 13 rule 10.

⚠️ Unauthenticated, like every route in this API. Anyone can approve anyone's match.
"""

from uuid import UUID

from db.models import Match
from fastapi import APIRouter, HTTPException, status as http
from schemas.enums import MatchStatus
from schemas.match import MatchRead
from sqlalchemy import update

from api import queue
from api.deps import SessionDep

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/{match_id}/approve", response_model=MatchRead)
async def approve(match_id: UUID, session: SessionDep) -> Match:
    """`tailored -> queued -> approved`, in one transaction.

    Both steps, deliberately. §6.1's machine puts `queued` between the two and says
    nothing may skip a transition; with notifications out of scope for the demo, nothing
    else writes `queued`, so an approve that jumped straight from `tailored` would leave a
    hole for M6 to fall into the moment it starts reading that state. Two conditional
    updates is four lines and no hole.

    Approving something already approved is a no-op that returns the same row. Approving
    something that was never tailored is a 409 — there is nothing to approve.
    """
    await session.execute(
        update(Match)
        .where(Match.id == match_id, Match.status == MatchStatus.TAILORED.value)
        .values(status=MatchStatus.QUEUED.value)
    )
    moved = await session.scalar(
        update(Match)
        .where(Match.id == match_id, Match.status == MatchStatus.QUEUED.value)
        .values(status=MatchStatus.APPROVED.value)
        .returning(Match.id)
    )
    await session.commit()

    row = await _require(session, match_id)
    if moved is None and row.status != MatchStatus.APPROVED.value:
        raise HTTPException(
            status_code=http.HTTP_409_CONFLICT,
            detail=f"cannot approve a match at '{row.status}'; it must be tailored first",
        )
    return row


@router.post("/{match_id}/skip", response_model=MatchRead)
async def skip(match_id: UUID, session: SessionDep) -> Match:
    """Legal from anywhere except `applied`. §6.1 draws `skipped` as reachable from any
    state, and the one thing it cannot undo is an application that already went out.

    Skipping an already-skipped match rewrites the same value and returns the same row,
    so idempotency falls out of the condition rather than needing a branch.
    """
    moved = await session.scalar(
        update(Match)
        .where(Match.id == match_id, Match.status != MatchStatus.APPLIED.value)
        .values(status=MatchStatus.SKIPPED.value)
        .returning(Match.id)
    )
    await session.commit()

    row = await _require(session, match_id)
    if moved is None:
        raise HTTPException(
            status_code=http.HTTP_409_CONFLICT,
            detail="cannot skip a match that has already been applied to",
        )
    return row


@router.post("/{match_id}/tailor", status_code=http.HTTP_202_ACCEPTED)
async def tailor(match_id: UUID, session: SessionDep) -> dict[str, str]:
    """Ask a worker to write the documents. 202, because it takes about a minute.

    No guard on the current status here on purpose: the stage acts only on a match at
    `discovered` and no-ops otherwise, so the state machine is enforced in one place
    rather than two that can disagree. A second press costs a queue message and nothing
    else.
    """
    await _require(session, match_id)
    queue.enqueue(queue.TAILOR_MATCH, str(match_id))
    return {"match_id": str(match_id), "status": "queued"}


async def _require(session: SessionDep, match_id: UUID) -> Match:
    row = await session.get(Match, match_id)
    if row is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND, detail="not found")
    await session.refresh(row)
    return row
