"""`approvals` — the human decision record."""

import uuid
from datetime import datetime

from schemas.enums import ApprovalChannel, ApprovalDecision
from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in, check_in_or_null
from db.mixins import CreatedAt, UUIDv7PK


class Approval(Base, UUIDv7PK, CreatedAt):
    __tablename__ = "approvals"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        check_in("channel", ApprovalChannel, name="channel"),
        check_in_or_null("decision", ApprovalDecision, name="decision"),
        # Partial unique: at most one *undecided* request per (match, channel), while
        # the full history of decided ones is preserved. This is what stops a Celery
        # retry of the M6 notify step double-messaging a real person — §3.4 treats
        # Telegram double-taps and worker restarts as expected, not exceptional.
        Index(
            "uq_approvals_pending",
            "match_id",
            "channel",
            unique=True,
            postgresql_where=text("decided_at IS NULL"),
        ),
    )
