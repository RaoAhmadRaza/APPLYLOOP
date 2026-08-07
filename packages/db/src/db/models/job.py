"""`jobs` — normalized postings from every source."""

import uuid
from datetime import datetime

from schemas.enums import AtsType, RemoteMode
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in_or_null
from db.mixins import Timestamps, UUIDv7PK


class Job(Base, UUIDv7PK, Timestamps):
    __tablename__ = "jobs"

    # No CHECK: source names churn with every new adapter and feed, and the adapter
    # itself is the gate. `ats_type` below is bounded and does get one.
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    # Nullable because M2's aggregator rows name a company we may have no registry row
    # for. SET NULL rather than CASCADE: retiring a board should not delete its history.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    # Whatever string the source gave, for display.
    location: Mapped[str | None] = mapped_column(Text)
    # What M4 filters on, with && against profiles.locations. Ashby returns
    # secondaryLocations[] and Recruitee/Workable return locations[], so a scalar alone
    # drops every multi-location posting.
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    # Not a boolean: hybrid roles are common and a bool silently flattens them. See
    # RemoteMode for the evidence from the six live ATS payloads.
    remote_mode: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    ats_type: Mapped[str | None] = mapped_column(Text)
    # Nullable: SmartRecruiters' list endpoint omits it, and Lever/Workable/Recruitee
    # each use a different date format that M1's adapters must parse per-source.
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL = still listed. Set when a board stops returning the posting. A timestamp
    # rather than a status enum: it answers "when", which a boolean throws away, and it
    # needs no CHECK to drop and re-add later.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # No default: an empty raw payload means the adapter dropped something.
    raw_json: Mapped[dict] = mapped_column(JSONB)
    # §4.2's cross-source merge key: normalized company|title|location. Written by the
    # dedupe pass, not by ingest — a fresh row is NULL, which reads as "not yet known to
    # be a duplicate", the safe default. NULL also means "un-keyable" (blank company or
    # title), and such a row is never merged with anything.
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    # NULL = this row is the survivor. Non-NULL = the id of the row that won its group.
    # That polarity, not self-pointing: it makes the downstream predicate
    # `canonical_id IS NULL` indexable, and every row predating this column is already
    # correctly marked with no backfill.
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )

    __table_args__ = (
        # The ingest dedupe key. Without it every M1 run duplicates every job.
        UniqueConstraint("source", "external_id"),
        check_in_or_null("ats_type", AtsType, name="ats_type"),
        check_in_or_null("remote_mode", RemoteMode, name="remote_mode"),
        Index("ix_jobs_locations", "locations", postgresql_using="gin"),
        # The access path for "close everything this board stopped returning", and for
        # M4's "score only open jobs". Partial, so it stays small as closed rows pile up.
        Index(
            "ix_jobs_company_id_open",
            "company_id",
            postgresql_where=text("closed_at IS NULL"),
        ),
        # "NULL = survivor" has to be a database fact, not a convention: a row pointing
        # at itself would be excluded from `canonical_id IS NULL` and silently vanish
        # from the deduped pool.
        CheckConstraint("canonical_id IS NULL OR canonical_id <> id", name="canonical_id_not_self"),
        # The dedupe pass's hot path is "which open rows still need a key?". After the
        # first backfill that must be an empty index scan, not a scan of every open row.
        Index(
            "ix_jobs_dedupe_key_open",
            "dedupe_key",
            postgresql_where=text("closed_at IS NULL"),
        ),
    )
