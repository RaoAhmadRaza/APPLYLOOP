"""`events` — the audit trail (§3.7: alert on volume, not just on errors)."""

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.mixins import CreatedAt


class Event(Base, CreatedAt):
    __tablename__ = "events"

    # bigserial, not uuidv7: append-only, internal, never exposed by URL, and the
    # highest-volume table here. 8 bytes beats 16 when it compounds.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Nullable: system events have no user, and losing the audit trail when a user is
    # deleted would defeat the point.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    __table_args__ = (Index("ix_events_user_id_created_at", "user_id", text("created_at DESC")),)
