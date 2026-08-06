"""`jobs` — normalized postings from every source.

Known M1 additions, verified against live responses from all six ATS APIs. Each is a
pure column add on what is currently a zero-row table, which §6.2 blesses:
  - `remote` cannot express hybrid (Recruitee has three non-exclusive booleans;
    Ashby has isRemote + workplaceType). M1 replaces it with `remote_mode`.
  - `location` scalar loses multi-location postings (Ashby secondaryLocations[],
    Recruitee/Workable locations[]). M1 adds `locations TEXT[]` + GIN.
  - `company` is raw text with no FK to `companies`. M1 adds `company_id`; nothing
    resolves companies until the registry exists.
"""

from datetime import datetime

from schemas.enums import AtsType
from sqlalchemy import Boolean, DateTime, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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
    location: Mapped[str | None] = mapped_column(Text)
    remote: Mapped[bool | None] = mapped_column(Boolean)
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
    )
