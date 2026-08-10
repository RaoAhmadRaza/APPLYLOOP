"""embeddings narrow to 768

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-10 13:05:00.000000

`job_embeddings.embedding` — halfvec(1536) to halfvec(768).

The OpenAI balance reached zero, so the chat provider became DeepSeek — which serves no
`/embeddings` route at all, on any host. Embeddings move to Gemini `text-embedding-004`,
whose native width is 768.

**This is the one change to this table that is not additive.** `job_embeddings` is keyed
`(job_id, model)` and `matching.embed.storage_model()` puts the model slug in that second
column, so two providers normally coexist as two rows and nothing needs a migration. The
*width* is different: it is the column's type, and one column cannot hold two of them.

**The existing rows are deleted, and they were already unreachable.** Every read filters
on `storage_model()` — `matching/match.py` — which returns the *currently configured*
slug. Nothing has been able to select a `text-embedding-3-small` vector since the setting
changed, so this deletes rows that were already invisible rather than losing anything a
query could still find. There is no index on this table yet and nothing references it, so
there is nothing to rebuild.

`downgrade` restores the type but not the data — stated plainly rather than implied. The
`DELETE` on the way back is what keeps the round-trip test honest: a 768-wide row cannot
live in a 1536-wide column either.

**What this invalidates, and does not fix.** `MATCH_THRESHOLD=20` and M4's 0.86 precision
were both measured in `text-embedding-3-small`'s vector space. A different model is a
different space, so both numbers describe a system that no longer exists — see
`docs/PROJECT_STATE.md`. Re-running `make verify-live-match` is the trigger, deliberately
out of scope here.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa  # noqa: F401 — the other versions carry it; autogenerate expects it
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Empty first: halfvec(1536) has no cast to halfvec(768), so the ALTER only succeeds
    # on a table with no rows to convert.
    op.execute("DELETE FROM job_embeddings")
    op.alter_column(
        "job_embeddings",
        "embedding",
        existing_type=pgvector.sqlalchemy.halfvec.HALFVEC(dim=1536),
        type_=pgvector.sqlalchemy.halfvec.HALFVEC(dim=768),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM job_embeddings")
    op.alter_column(
        "job_embeddings",
        "embedding",
        existing_type=pgvector.sqlalchemy.halfvec.HALFVEC(dim=768),
        type_=pgvector.sqlalchemy.halfvec.HALFVEC(dim=1536),
        existing_nullable=False,
    )
