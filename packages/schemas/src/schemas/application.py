"""`applications` — CLAUDE.md §3.4: this row is the LOCK, not the log.

The row is inserted BEFORE the submit attempt, and UNIQUE(match_id, method) is what
makes a retry a no-op. A plain INSERT against that constraint raises IntegrityError,
so duplicate suppression is loud by default and you must opt into silence with
ON CONFLICT DO NOTHING. That polarity is deliberate.

`submitted_at` is nullable with no default: it means "this happened", and a default
would forge it.
"""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema
from schemas.enums import ApplicationStatus, ApplyMethod


class ApplicationBase(Schema):
    match_id: UUID
    method: ApplyMethod
    status: ApplicationStatus = ApplicationStatus.PENDING


class ApplicationCreate(ApplicationBase):
    pass


class ApplicationUpdate(Schema):
    status: ApplicationStatus | None = None
    submitted_at: datetime | None = None
    confirmation: str | None = None
    error: str | None = None


class ApplicationRead(ApplicationBase):
    id: UUID
    submitted_at: datetime | None = None
    confirmation: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
