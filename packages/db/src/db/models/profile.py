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

    # One profile per user. The rest of the schema already assumes this: `matches` is
    # unique on (user_id, job_id) and carries no profile_id, so with two profiles a job
    # could be a good fit for one and a poor fit for the other with nowhere to record
    # the difference, and M4 would have no way to choose which profile to score against.
    # Supporting multiple target-role profiles is a real design change — it needs
    # matches.profile_id and a different unique key — not a relaxed constraint here.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
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
        # No separate index on user_id — the unique constraint above already provides one.
        Index("ix_profiles_locations", "locations", postgresql_using="gin"),
    )
