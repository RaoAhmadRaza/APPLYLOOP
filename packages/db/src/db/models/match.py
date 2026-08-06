"""`matches` — carries the pipeline state machine (§6.1)."""

import uuid

from schemas.enums import MatchLabel, MatchStatus
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in, check_in_or_null
from db.mixins import Timestamps, UUIDv7PK


class Match(Base, UUIDv7PK, Timestamps):
    __tablename__ = "matches"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # Nullable until M4 scores it. No threshold is encoded anywhere in this repo —
    # Part 14 defers it to the golden set.
    score: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(Text)
    reasons_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=MatchStatus.DISCOVERED.value)

    __table_args__ = (
        # Without this a re-run of the matcher fans out duplicate matches, which
        # becomes duplicate documents, which becomes duplicate approval messages to a
        # real person. §5.4 keys every pipeline step on (user_id, job_id); this is
        # what makes that key mean something.
        UniqueConstraint("user_id", "job_id"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        check_in("status", MatchStatus, name="status"),
        check_in_or_null("label", MatchLabel, name="label"),
        Index("ix_matches_status_user_id", "status", "user_id"),
    )

    # No transition trigger. Legal transitions are enforced by each worker's
    # conditional `UPDATE ... WHERE status = <expected>` plus a rowcount check, which
    # is a smaller diff AND doubles as optimistic concurrency for Celery retries: two
    # workers racing on one match, exactly one wins, the loser no-ops.
    # Add a trigger when a non-worker writer (admin panel, manual SQL) starts touching
    # this column — that is when app-level enforcement stops being sufficient.
