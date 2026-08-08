"""profile work auth regions

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08 14:20:00.000000

`profiles.work_auth_regions` — WHERE the authorisation in `work_auth` applies.

M4's first live gate run found the gap. `work_auth` is a country-less scalar, so a résumé
reading "UK citizen." reaches the matcher as the bare word `citizen`, and the model has
nothing to contradict when a SpaceX posting says "must be a U.S. person" — it scored 94 on
a role that candidate legally cannot hold. Three of the eleven hard negatives a human had
to correct in the golden set are the same shape.

**A new column, not a changed meaning (§6.2).** `work_auth` keeps its exact semantics and
its CHECK constraint, so M3's gate assertions — which compare it exactly — are untouched.

Nullable with no default, and the distinction between NULL and `{}` is load-bearing:

    NULL   the résumé did not state it. Never drops anything (filters.py's polarity rule:
           every filter passes when either side is silent).
    {}     the résumé stated authorisation and it is nowhere — someone who needs
           sponsorship in every country they named.

Values are ISO-3166 alpha-2 plus `EU` as a bloc token. No CHECK constraint: the set is
large, `EU` is not ISO, and a constraint here would reject a correct value from a résumé
naming a country nobody thought to enumerate. The parse prompt constrains the vocabulary;
a wrong token costs one bad filter decision, a rejected INSERT costs the whole parse.

No data step. Existing profiles keep NULL, which is the correct state for a column no
parse has ever populated — and NULL is the value that changes no behaviour.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401 — generated VECTOR/HALFVEC columns need it
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("profiles", sa.Column("work_auth_regions", sa.ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("profiles", "work_auth_regions")
