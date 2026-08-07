"""`evidence` — the facts store half of the evidence vault (§3.3)."""

import uuid

from schemas.enums import EvidenceKind, EvidenceOrigin
from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.constraints import check_in
from db.mixins import CreatedAt, UUIDv7PK


class Evidence(Base, UUIDv7PK, CreatedAt):
    __tablename__ = "evidence"

    # CASCADE: the vault is meaningless without the profile it describes, and §3.3's
    # whole point is that a claim is only valid relative to one person's résumé.
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    # The claim, verbatim from the résumé. `vault.py` refuses to write a row whose text
    # is not present in that profile's `master_resume` — the guarantee §3.3 rests on.
    text: Mapped[str] = mapped_column(Text)
    # What M5 cites when it says a generated bullet is traceable: `work[1].highlights[0]`.
    # Nullable because a user-added claim has no position in a parsed document.
    source: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, server_default=EvidenceOrigin.USER.value)

    # CreatedAt, not Timestamps: a claim is inserted or deleted, never edited in place.
    # A re-parse rebuilds the `parsed` set rather than updating rows, because a changed
    # bullet is a different claim, not the same one with new text.

    __table_args__ = (
        # Makes a re-parse idempotent: the same résumé yields the same claims, and the
        # database refuses the second copy rather than the code checking for it.
        #
        # ponytail: a btree index row caps around 2704 bytes, so a single bullet longer
        # than that would fail to insert. Résumé bullets are two orders of magnitude
        # short of it. Hash the text if a real résumé ever proves otherwise.
        UniqueConstraint("profile_id", "kind", "text"),
        check_in("kind", EvidenceKind, name="kind"),
        check_in("origin", EvidenceOrigin, name="origin"),
        # M5's access path: every claim for one profile, usually filtered by kind.
        Index("ix_evidence_profile_id_kind", "profile_id", "kind"),
    )
