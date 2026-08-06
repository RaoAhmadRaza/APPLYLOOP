"""`users`."""

from schemas.enums import UserPlan
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in
from db.mixins import CreatedAt, UUIDv7PK


class User(Base, UUIDv7PK, CreatedAt):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, unique=True)
    # Provider-agnostic: Clerk vs Supabase Auth is undecided (§7.1), and no provider
    # type should leak into the schema.
    auth_id: Mapped[str] = mapped_column(Text, unique=True)
    plan: Mapped[str] = mapped_column(Text, server_default=UserPlan.FREE.value)

    __table_args__ = (check_in("plan", UserPlan, name="plan"),)
