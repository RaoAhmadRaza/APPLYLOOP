"""`profiles`."""

import uuid

from schemas.enums import Seniority, WorkAuth
from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in_or_null
from db.mixins import Timestamps, UUIDv7PK


class Profile(Base, UUIDv7PK, Timestamps):
    __tablename__ = "profiles"

    # Deliberately NOT unique: multiple target-role profiles per user is plausible,
    # and nothing at M0 depends on the constraint either way.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    master_resume: Mapped[str | None] = mapped_column(Text)
    parsed_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prefs_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    work_auth: Mapped[str | None] = mapped_column(Text)
    # TEXT[] over a join table: these are short lists of free-text strings filtered
    # with && (overlap). A join table would buy referential integrity we don't need
    # and cost a join on the hottest M4 query.
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    seniority: Mapped[str | None] = mapped_column(Text)
    salary_floor: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        check_in_or_null("work_auth", WorkAuth, name="work_auth"),
        check_in_or_null("seniority", Seniority, name="seniority"),
        Index("ix_profiles_user_id", "user_id"),
        Index("ix_profiles_locations", "locations", postgresql_using="gin"),
    )
