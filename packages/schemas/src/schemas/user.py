"""`users`. `auth_id` is provider-agnostic text because Clerk vs Supabase Auth is
still undecided (§7.1) — no provider type leaks into the schema."""

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr

from schemas.common import Schema
from schemas.enums import UserPlan


class UserBase(Schema):
    email: EmailStr
    auth_id: str
    plan: UserPlan = UserPlan.FREE


class UserCreate(UserBase):
    pass


class UserUpdate(Schema):
    email: EmailStr | None = None
    auth_id: str | None = None
    plan: UserPlan | None = None


class UserRead(UserBase):
    id: UUID
    created_at: datetime
