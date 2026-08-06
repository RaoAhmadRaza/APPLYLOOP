"""`job_embeddings` — a separate table, not a `jobs.embedding` column.

§6 sketches the column inline. Keeping it there makes an embedding-provider swap an
`ALTER COLUMN TYPE`: an ACCESS EXCLUSIVE full rewrite of the largest table, with no
way to dual-run old and new models. Part 14 lists the provider as an open question,
so the swap is expected.

Here it is additive — insert rows under a new `model`, add one partial index, `jobs`
is never touched — and the composite PK doubles as the M4 embed worker's idempotency
key.

No HNSW index at M0. pgvector's own guidance is to build after loading data, and at
zero rows exact KNN gives 100% recall, which is the baseline `/evals` needs to judge
scoring quality against. Trigger to add it: >100k rows or p95 match query >500ms.
"""

import uuid

from pgvector.sqlalchemy import HALFVEC
from schemas.job_embedding import EMBEDDING_DIM
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.mixins import CreatedAt


class JobEmbedding(Base, CreatedAt):
    __tablename__ = "job_embeddings"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    # e.g. "openai/text-embedding-3-small@1536". Part of the PK so one job can hold
    # several models' vectors at once during a migration between providers.
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    # halfvec (float16): half the table and index memory for negligible recall loss,
    # and it raises the HNSW dimension ceiling from 2000 to 4000. Retrofitting float16
    # onto millions of float32 rows later is a painful backfill; starting here is free.
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIM))
