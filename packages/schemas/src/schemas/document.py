"""`documents` — generated resume and cover letter per match."""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema
from schemas.enums import DocumentType


class DocumentBase(Schema):
    match_id: UUID
    type: DocumentType
    storage_url: str
    gdrive_url: str | None = None
    version: int = 1


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(Schema):
    storage_url: str | None = None
    gdrive_url: str | None = None


class DocumentRead(DocumentBase):
    id: UUID
    created_at: datetime
