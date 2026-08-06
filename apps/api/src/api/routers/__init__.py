"""Routers. One CRUD router per table, all built from the same factory.

`job_embeddings` is deliberately absent: its primary key is composite (job_id, model),
which the single-id CRUD factory cannot express, and nothing needs to write embeddings
over HTTP until M4 does it from a worker. Adding a bespoke router for it now would be
building an endpoint with no caller.
"""

from typing import Any

from db import models
from fastapi import APIRouter
from pydantic import BaseModel
from schemas import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
    ApprovalCreate,
    ApprovalRead,
    ApprovalUpdate,
    CompanyCreate,
    CompanyRead,
    CompanyUpdate,
    DocumentCreate,
    DocumentRead,
    DocumentUpdate,
    EventCreate,
    EventRead,
    EventUpdate,
    JobCreate,
    JobRead,
    JobUpdate,
    MatchCreate,
    MatchRead,
    MatchUpdate,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    UserCreate,
    UserRead,
    UserUpdate,
)

from api.crud import make_crud_router

# (model, create, update, read, prefix, tag). Annotated because a bare heterogeneous
# list of tuples collapses to a join type mypy cannot pass through.
_TABLES: list[tuple[Any, type[BaseModel], type[BaseModel], type[BaseModel], str, str]] = [
    (models.Company, CompanyCreate, CompanyUpdate, CompanyRead, "companies", "companies"),
    (models.User, UserCreate, UserUpdate, UserRead, "users", "users"),
    (models.Profile, ProfileCreate, ProfileUpdate, ProfileRead, "profiles", "profiles"),
    (models.Job, JobCreate, JobUpdate, JobRead, "jobs", "jobs"),
    (models.Match, MatchCreate, MatchUpdate, MatchRead, "matches", "matches"),
    (models.Document, DocumentCreate, DocumentUpdate, DocumentRead, "documents", "documents"),
    (
        models.Application,
        ApplicationCreate,
        ApplicationUpdate,
        ApplicationRead,
        "applications",
        "applications",
    ),
    (models.Approval, ApprovalCreate, ApprovalUpdate, ApprovalRead, "approvals", "approvals"),
]


def build_crud_routers() -> list[APIRouter]:
    routers = [
        make_crud_router(
            model=model,
            create_schema=create,
            update_schema=update,
            read_schema=read,
            prefix=prefix,
            tag=tag,
        )
        for model, create, update, read, prefix, tag in _TABLES
    ]
    # events.id is bigserial, not uuid — the only table whose key type differs.
    routers.append(
        make_crud_router(
            model=models.Event,
            create_schema=EventCreate,
            update_schema=EventUpdate,
            read_schema=EventRead,
            prefix="events",
            tag="events",
            id_type=int,
        )
    )
    return routers
