"""Wire format shared across pipeline stages. See CLAUDE.md §5.3."""

from schemas.application import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from schemas.approval import ApprovalCreate, ApprovalRead, ApprovalUpdate
from schemas.common import Page, Schema
from schemas.company import CompanyCreate, CompanyRead, CompanyUpdate
from schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from schemas.event import EventCreate, EventRead, EventUpdate
from schemas.evidence import EvidenceCreate, EvidenceRead, EvidenceUpdate
from schemas.job import JobCreate, JobRead, JobUpdate
from schemas.job_embedding import (
    EMBEDDING_DIM,
    JobEmbeddingCreate,
    JobEmbeddingRead,
    JobEmbeddingUpdate,
)
from schemas.match import MatchCreate, MatchRead, MatchUpdate
from schemas.prefs import Prefs
from schemas.profile import ProfileCreate, ProfileRead, ProfileUpdate
from schemas.resume import ParsedResume
from schemas.user import UserCreate, UserRead, UserUpdate

__all__ = [
    "EMBEDDING_DIM",
    "ApplicationCreate",
    "ApplicationRead",
    "ApplicationUpdate",
    "ApprovalCreate",
    "ApprovalRead",
    "ApprovalUpdate",
    "CompanyCreate",
    "CompanyRead",
    "CompanyUpdate",
    "DocumentCreate",
    "DocumentRead",
    "DocumentUpdate",
    "EventCreate",
    "EventRead",
    "EventUpdate",
    "EvidenceCreate",
    "EvidenceRead",
    "EvidenceUpdate",
    "JobCreate",
    "JobEmbeddingCreate",
    "JobEmbeddingRead",
    "JobEmbeddingUpdate",
    "JobRead",
    "JobUpdate",
    "MatchCreate",
    "MatchRead",
    "MatchUpdate",
    "Page",
    "ParsedResume",
    "Prefs",
    "ProfileCreate",
    "ProfileRead",
    "ProfileUpdate",
    "Schema",
    "UserCreate",
    "UserRead",
    "UserUpdate",
]
