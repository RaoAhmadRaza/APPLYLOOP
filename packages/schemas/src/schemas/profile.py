"""`profiles`. Deliberately not one-per-user: multiple target-role profiles is plausible
and nothing at M0 depends on the constraint either way."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from schemas.common import Schema
from schemas.enums import Seniority, WorkAuth


class ProfileBase(Schema):
    user_id: UUID
    master_resume: str | None = None
    parsed_json: dict[str, Any] = Field(default_factory=dict)
    prefs_json: dict[str, Any] = Field(default_factory=dict)
    work_auth: WorkAuth | None = None
    locations: list[str] = Field(default_factory=list)
    seniority: Seniority | None = None
    salary_floor: int | None = None


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(Schema):
    master_resume: str | None = None
    parsed_json: dict[str, Any] | None = None
    prefs_json: dict[str, Any] | None = None
    work_auth: WorkAuth | None = None
    locations: list[str] | None = None
    seniority: Seniority | None = None
    salary_floor: int | None = None


class ProfileRead(ProfileBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
