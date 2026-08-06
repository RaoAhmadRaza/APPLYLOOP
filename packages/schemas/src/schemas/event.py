"""`events` — the audit trail.

`id` is bigserial, not uuidv7: append-only, internal, never exposed by URL, and the
highest-volume table in the system, so 8 bytes beats 16. `user_id` is nullable
because system events have no user.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from schemas.common import Schema


class EventBase(Schema):
    user_id: UUID | None = None
    type: str
    payload_json: dict[str, Any] = Field(default_factory=dict)


class EventCreate(EventBase):
    pass


class EventUpdate(Schema):
    """Events are append-only. Nothing here is updatable by design."""


class EventRead(EventBase):
    id: int
    created_at: datetime
