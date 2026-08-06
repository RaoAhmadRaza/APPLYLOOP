"""`jobs` — normalized postings from every source.

Known M1 addition: `company` is raw text with no FK to `companies`. `company_id` lands
in M1 alongside the registry code that resolves it — adding the column now would give
it no writer. §6.2 blesses column adds, and this table is the one that stays cheap to
extend.
"""

from datetime import datetime

from schemas.enums import AtsType, RemoteMode
from sqlalchemy import DateTime, Index, Text, UniqueConstraint, text
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
    # No default: an empty raw payload means the adapter dropped something.
    raw_json: Mapped[dict] = mapped_column(JSONB)

    __table_args__ = (
        # The ingest dedupe key. Without it every M1 run duplicates every job.
        UniqueConstraint("source", "external_id"),
        check_in_or_null("ats_type", AtsType, name="ats_type"),
        check_in_or_null("remote_mode", RemoteMode, name="remote_mode"),
        Index("ix_jobs_locations", "locations", postgresql_using="gin"),
    )
