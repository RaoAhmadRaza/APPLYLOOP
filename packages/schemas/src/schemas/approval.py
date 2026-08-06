"""`approvals` — the human decision record.

A partial unique index on (match_id, channel) WHERE decided_at IS NULL allows at most
one *undecided* request per channel while preserving the full history of decided ones.
That is what stops a Celery retry of the notify step double-messaging a real person
(§3.4: Telegram double-taps and worker restarts are expected, not exceptional).
"""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema
from schemas.enums import ApprovalChannel, ApprovalDecision


class ApprovalBase(Schema):
    match_id: UUID
    channel: ApprovalChannel


class ApprovalCreate(ApprovalBase):
    pass


class ApprovalUpdate(Schema):
    sent_at: datetime | None = None
    decided_at: datetime | None = None
    decision: ApprovalDecision | None = None


class ApprovalRead(ApprovalBase):
    id: UUID
    sent_at: datetime | None = None
    decided_at: datetime | None = None
    decision: ApprovalDecision | None = None
    created_at: datetime
