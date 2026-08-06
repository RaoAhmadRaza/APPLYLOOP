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
