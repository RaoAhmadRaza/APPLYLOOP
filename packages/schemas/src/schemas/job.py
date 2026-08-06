"""`jobs` — normalized postings from every source. Dedupe key is (source, external_id).

`location` and `locations` are both here on purpose: the scalar is whatever string the
source gave, for display; the array is what M4 filters on with `&&`. Ashby returns
`secondaryLocations[]` and Recruitee/Workable return `locations[]`, so a single scalar
drops every multi-location posting.

`closed_at` is read-only over HTTP: a job is never *created* closed, and only the
ingest stage decides a posting has stopped being listed.
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
    company_id: UUID | None = None
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
    company_id: UUID | None = None
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
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
