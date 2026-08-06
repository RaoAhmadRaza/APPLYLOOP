"""Every model, re-exported.

This file is load-bearing. `Base.metadata` only knows about tables whose module has
been imported, and Alembic autogenerate will happily emit `DROP TABLE` for any model
it cannot see. Adding a model without adding it here is a silent data-loss bug.
"""

from db.models.application import Application
from db.models.approval import Approval
from db.models.company import Company
from db.models.document import Document
from db.models.event import Event
from db.models.job import Job
from db.models.job_embedding import JobEmbedding
from db.models.match import Match
from db.models.profile import Profile
from db.models.user import User

__all__ = [
    "Application",
    "Approval",
    "Company",
    "Document",
    "Event",
    "Job",
    "JobEmbedding",
    "Match",
    "Profile",
    "User",
]
