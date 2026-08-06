"""`companies` — the ATS slug registry (§4.3, the moat)."""

from datetime import datetime

from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in
from db.mixins import Timestamps, UUIDv7PK


class Company(Base, UUIDv7PK, Timestamps):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    ats_type: Mapped[str] = mapped_column(Text)
    ats_slug: Mapped[str] = mapped_column(Text)
    last_seen_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=CompanyStatus.ACTIVE.value)

    __table_args__ = (
        # The registry's natural key. Two rows for the same board would split the
        # change-detection state in M1.
        UniqueConstraint("ats_type", "ats_slug"),
        check_in("ats_type", AtsType, name="ats_type"),
        check_in("status", CompanyStatus, name="status"),
        # "Which boards are due for a refresh." Partial, so it stays small as retired
        # slugs accumulate.
        Index(
            "ix_companies_jobs_last_run",
            "jobs_last_run",
            postgresql_where=text("status = 'active'"),
        ),
    )
