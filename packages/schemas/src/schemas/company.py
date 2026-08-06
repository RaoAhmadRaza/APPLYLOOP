"""`companies` — the ATS slug registry. CLAUDE.md §4.3 calls this the moat;
§6.1 says treat schema changes here as seriously as an API break."""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema
from schemas.enums import AtsType, CompanyStatus


class CompanyBase(Schema):
    name: str
    domain: str | None = None
    ats_type: AtsType
    ats_slug: str
    status: CompanyStatus = CompanyStatus.ACTIVE


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(Schema):
    name: str | None = None
    domain: str | None = None
    ats_type: AtsType | None = None
    ats_slug: str | None = None
    status: CompanyStatus | None = None
    last_seen_ok: datetime | None = None
    jobs_last_run: datetime | None = None


class CompanyRead(CompanyBase):
    id: UUID
    last_seen_ok: datetime | None = None
    jobs_last_run: datetime | None = None
    created_at: datetime
    updated_at: datetime
