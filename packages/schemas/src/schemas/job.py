"""`jobs` — normalized postings from every source. Dedupe key is (source, external_id).

`location` and `locations` are both here on purpose: the scalar is whatever string the
source gave, for display; the array is what M4 filters on with `&&`. Ashby returns
`secondaryLocations[]` and Recruitee/Workable return `locations[]`, so a single scalar
drops every multi-location posting.

Known M1 addition: `company` is raw text with no FK to `companies`. `company_id` lands
in M1 alongside the registry code that resolves it — there is no resolver to populate
it today.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from schemas.common import Schema
from schemas.enums import AtsType, RemoteMode


class JobBase(Schema):
    # Not an enum: source names churn with every new adapter and feed, and the
    # adapter itself is the gate. `ats_type` below IS bounded.
    source: str
    external_id: str
    title: str
    company: str
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    remote_mode: RemoteMode | None = None
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
    locations: list[str] | None = None
    remote_mode: RemoteMode | None = None
    description: str | None = None
    url: str | None = None
    ats_type: AtsType | None = None
    posted_at: datetime | None = None
    raw_json: dict[str, Any] | None = None


class JobRead(JobBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
