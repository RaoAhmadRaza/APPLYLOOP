"""`documents` — generated resume and cover letter per match."""

import uuid

from schemas.enums import DocumentType
from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in
from db.mixins import CreatedAt, UUIDv7PK


class Document(Base, UUIDv7PK, CreatedAt):
    __tablename__ = "documents"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    storage_url: Mapped[str] = mapped_column(Text)
    gdrive_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint("match_id", "type", "version"),
        check_in("type", DocumentType, name="type"),
    )
