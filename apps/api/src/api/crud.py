"""Generic CRUD router factory.

Used nine times, once per table. That is real repetition being removed, not a
speculative abstraction — nine hand-written routers would be ~350 lines of identical
code drifting apart on the first bug fix.

Responses are bare pydantic models rather than a success/data/error envelope: the M8
dashboard and M9 extension generate their clients from this OpenAPI schema, and an
envelope makes those generated clients worse.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from schemas.common import Page
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.deps import SessionDep
from api.settings import settings

# Anything with an `id` column; the models are not a common base class beyond
# db.base.Base, so this stays loose on purpose.
Model = Any


def make_crud_router(
    *,
    model: Model,
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    read_schema: type[BaseModel],
    prefix: str,
    tag: str,
    id_type: type = UUID,
) -> APIRouter:
    router = APIRouter(prefix=f"/{prefix}", tags=[tag])

    @router.post("", response_model=read_schema, status_code=status.HTTP_201_CREATED)
    async def create(payload: create_schema, session: SessionDep) -> Model:  # type: ignore[valid-type]
        row = model(**payload.model_dump())
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # 409, not 500. A unique-constraint hit is the database refusing a
            # duplicate exactly as designed (§3.4) — it is a client-visible outcome,
            # not a server fault.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"conflicts with an existing {prefix} row",
            ) from exc
        await session.refresh(row)
        return row

    @router.get("/{row_id}", response_model=read_schema)
    async def read_one(row_id: id_type, session: SessionDep) -> Model:  # type: ignore[valid-type]
        row = await session.get(model, row_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        return row

    @router.get("", response_model=Page[read_schema])  # type: ignore[valid-type]
    async def read_many(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=settings.max_page_size)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page[Any]:
        total = await session.scalar(select(func.count()).select_from(model))
        rows = (await session.scalars(select(model).limit(limit).offset(offset))).all()
        return Page[Any](
            items=[read_schema.model_validate(r) for r in rows],
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    @router.patch("/{row_id}", response_model=read_schema)
    async def update(row_id: id_type, payload: update_schema, session: SessionDep) -> Model:  # type: ignore[valid-type]
        row = await session.get(model, row_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        # exclude_unset so PATCH means "change these fields", not "null the rest".
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"conflicts with an existing {prefix} row",
            ) from exc
        await session.refresh(row)
        return row

    @router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(row_id: id_type, session: SessionDep) -> None:
        row = await session.get(model, row_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        await session.delete(row)
        await session.commit()

    return router
