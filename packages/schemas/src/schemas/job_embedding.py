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
"""Column dimension — `text-embedding-3-small`'s native width.

**Changing this is not additive, unlike changing the model *name*.** The name is a column
value, so two providers coexist as two rows; the width is the column's type and one column
cannot hold two. It moves with a migration, and the rows written at the old width do not
survive it.

Briefly 768 on 2026-08-10, for Gemini. Reverted the same day: keeping 1536 keeps M4's
measured precision and its threshold valid, which a re-embed into a new vector space would
have thrown away. Embeddings stayed on OpenAI; only chat moved to DeepSeek.

There is no `dim` column: the type carries it, so this constant and the schema migration
are one fact in two places and must land together."""


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
