"""`job_embeddings` — deliberately a separate table, not a `jobs.embedding` column.

CLAUDE.md §6 sketches the column inline. Keeping it there means swapping embedding
provider is `ALTER COLUMN TYPE` — an ACCESS EXCLUSIVE full rewrite of the largest
table, with no way to dual-run old and new models. Part 14 lists the provider as an
open question, so a swap is expected, not hypothetical.

Here a swap is additive: insert rows under a new `model`, add one partial index,
`jobs` is never touched. PK (job_id, model) doubles as the M4 embed worker's
idempotency key.

Storage is `halfvec` (float16) — half the table and index memory for negligible
recall loss, and it raises the HNSW dimension ceiling from 2000 to 4000.
"""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema

EMBEDDING_DIM = 1536
"""Column dimension. A model with a different native dimension gets its own column
or table — both pure additions. There is no `dim` column: the type carries it."""


class JobEmbeddingBase(Schema):
    job_id: UUID
    # Not `model_name` — pydantic reserves the `model_` prefix and would warn.
    model: str  # e.g. "openai/text-embedding-3-small@1536"
    embedding: list[float]


class JobEmbeddingCreate(JobEmbeddingBase):
    pass


class JobEmbeddingUpdate(Schema):
    embedding: list[float] | None = None


class JobEmbeddingRead(JobEmbeddingBase):
    created_at: datetime
