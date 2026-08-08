"""`profiles`. One per user — `profiles.user_id` is UNIQUE as of migration 0003.

`matches` is keyed on `(user_id, job_id)` and carries no `profile_id`, so a second
profile would have nowhere to record its own scores. Supporting multiple target-role
profiles needs `matches.profile_id` and a different unique key; it is a design change,
not a relaxed constraint. See the model for the long version.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from schemas.common import Schema
from schemas.enums import Seniority, WorkAuth


class ProfileBase(Schema):
    user_id: UUID
    master_resume: str | None = None
    # Set by the upload endpoint, not by a client. Exposed because the dashboard needs
    # to know whether a résumé is on file at all.
    resume_url: str | None = None
    parsed_json: dict[str, Any] = Field(default_factory=dict)
    prefs_json: dict[str, Any] = Field(default_factory=dict)
    work_auth: WorkAuth | None = None
    # Where `work_auth` applies. ISO-3166 alpha-2 plus `EU`. `None` is silence and never
    # drops anything; `[]` is "stated, and nowhere". Derived deterministically in
    # `profiles/derive.py`, never emitted by a model — it is a filter and prompt input.
    work_auth_regions: list[str] | None = None
    locations: list[str] = Field(default_factory=list)
    seniority: Seniority | None = None
    salary_floor: int | None = None


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(Schema):
    master_resume: str | None = None
    resume_url: str | None = None
    parsed_json: dict[str, Any] | None = None
    prefs_json: dict[str, Any] | None = None
    work_auth: WorkAuth | None = None
    work_auth_regions: list[str] | None = None
    locations: list[str] | None = None
    seniority: Seniority | None = None
    salary_floor: int | None = None


class ProfileRead(ProfileBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
