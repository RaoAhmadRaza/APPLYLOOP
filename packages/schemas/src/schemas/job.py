"""`jobs` — normalized postings from every source. Dedupe key is (source, external_id).

Known M1 additions, verified against live responses from all six ATS APIs. All three
are additive column adds on a zero-row table, which §6.2 blesses:
  - `remote: bool` is lossy. Ashby has isRemote + workplaceType, Recruitee has three
    non-exclusive booleans, SmartRecruiters has remote + hybrid. Hybrid can't be
    represented. M1 replaces it with `remote_mode`.
  - `location: str` is lossy. Ashby has secondaryLocations[], Recruitee and Workable
    have locations[]. M1 adds `locations: list[str]`.
  - `company` is raw text with no FK to `companies`. M1 adds `company_id`; nothing
    resolves companies until the registry exists.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from schemas.common import Schema
from schemas.enums import AtsType


class JobBase(Schema):
    # Not an enum: source names churn with every new adapter and feed, and the
    # adapter itself is the gate. `ats_type` below IS bounded.
    source: str
    external_id: str
    title: str
    company: str
    location: str | None = None
    remote: bool | None = None
    description: str | None = None
    url: str
    ats_type: AtsType | None = None
    posted_at: datetime | None = None
    raw_json: dict[str, Any]


class JobCreate(JobBase):
    pass


class JobUpdate(Schema):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    remote: bool | None = None
    description: str | None = None
    url: str | None = None
    ats_type: AtsType | None = None
    posted_at: datetime | None = None
    raw_json: dict[str, Any] | None = None


class JobRead(JobBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
