"""`applications` — CLAUDE.md §3.4: this row is the LOCK, not the log."""

import uuid
from datetime import datetime

from schemas.enums import ApplicationStatus, ApplyMethod
from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in
from db.mixins import Timestamps, UUIDv7PK


class Application(Base, UUIDv7PK, Timestamps):
    __tablename__ = "applications"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    # §5.2: the two apply paths are a data boundary, not a class hierarchy. Both write
    # here with a different `method`. There is no ApplyStrategy ABC.
    method: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=ApplicationStatus.PENDING.value)
    # Nullable, no default: it means "this happened", and a default would forge it.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # The lock. Insert this row BEFORE the submit attempt, never after. A plain
        # INSERT that conflicts raises IntegrityError, so duplicate suppression is
        # loud by default and callers must opt into silence with ON CONFLICT DO
        # NOTHING. Never do check-then-act in Python (Part 13 rule 10).
        UniqueConstraint("match_id", "method"),
        check_in("method", ApplyMethod, name="method"),
        check_in("status", ApplicationStatus, name="status"),
    )
