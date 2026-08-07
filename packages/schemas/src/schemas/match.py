"""`matches` — carries the pipeline state machine (§6.1).

No score threshold is encoded anywhere. Part 14 lists it as deliberately deferred
until the M4 golden set sets it empirically; hardcoding a magic number now would be
inventing an answer the data hasn't given yet.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from schemas.common import Schema
from schemas.enums import MatchLabel, MatchStatus


class MatchBase(Schema):
    user_id: UUID
    job_id: UUID
    score: int | None = Field(default=None, ge=0, le=100)
    label: MatchLabel | None = None
    reasons_json: dict[str, Any] = Field(default_factory=dict)
    status: MatchStatus = MatchStatus.DISCOVERED


class MatchCreate(MatchBase):
    pass


class MatchUpdate(Schema):
    score: int | None = Field(default=None, ge=0, le=100)
    label: MatchLabel | None = None
    reasons_json: dict[str, Any] | None = None
    status: MatchStatus | None = None


class MatchRead(MatchBase):
    id: UUID
    created_at: datetime
    updated_at: datetime


class MatchFacts(Schema):
    """What the model returns for one (profile, job) pair. **Deliberately no score.**

    The model partitions the requirements; Python does the arithmetic. That is
    `profiles/derive.py`'s split one milestone later, and it is what makes a threshold
    mean anything: a model-emitted 0–100 clusters on 85/90/75 and its distribution moves
    with every model version, so a cut calibrated on fifty pairs on Monday is measuring
    something else by Wednesday. A ratio over counted facts moves only when the facts do.

    `met` and `missing` hold **verbatim spans from the job description**, not paraphrases.
    That is what lets the live gate assert every reason is findable in the posting — a
    requirement the posting never stated is M5's fabrication problem arriving early, on
    text a user will read and act on.
    """

    met: list[str]
    missing: list[str]
    summary: str


class MatchReasons(Schema):
    """The `reasons_json` contract — the gate clause "every score carries a reason".

    Here rather than in the stage for the reason `ParsedResume` is: it is the shape of a
    database column, and M5, M6 and M8 all read it. `coverage` is None only when the
    posting stated no requirements at all, which is also the one case that produces a
    NULL score.

    `similarity` is recorded but is **not** a term in the score. Cosine decides who gets
    asked, not how good the answer is — §7.2's whole complaint about cosine alone.
    """

    summary: str
    met: list[str]
    missing: list[str]
    coverage: float | None
    similarity: float
    seniority_delta: int | None
    filters_passed: list[str]
    threshold: int
    model: str
    embed_model: str
