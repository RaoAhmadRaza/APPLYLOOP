"""Shared pydantic base and the list envelope."""

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """Base for every wire type. `from_attributes` lets Read models load from ORM rows."""

    model_config = ConfigDict(from_attributes=True)


class Page[T](Schema):
    """Bounded list response. Unbounded list endpoints are a footgun once M1 lands
    100k rows in `jobs`, so every collection route returns this."""

    items: list[T]
    total: int
    limit: int
    offset: int
