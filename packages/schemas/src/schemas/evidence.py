"""`evidence` — the facts store half of the evidence vault (§3.3).

The vault is two parts: `profiles.master_resume`, which already existed, and this. Every
row is one atomic claim about the user, and **every row's `text` appears verbatim in
that profile's `master_resume`** — verified when it is written, not trusted.

That verification is the point. §3.3 makes M5's validator the guardrail against
fabrication, but a validator that grounds bullets against an unverified vault proves
nothing: if the *parser* invents a skill, M5 will happily find it "traceable". Checking
at write time is what makes the vault worth diffing against.

`source` is the pointer M5 cites — `work[1].highlights[0]`, `skills[3]`. §3.3 says
"anything not traceable to **a source**", singular, per claim.

Not a JSONB key on `profiles`: `parsed_json` is *derived* and is rebuilt on every
re-parse, while `origin='user'` claims must survive one. Different lifecycle, different
table.
"""

from datetime import datetime
from uuid import UUID

from schemas.common import Schema
from schemas.enums import EvidenceKind, EvidenceOrigin


class EvidenceBase(Schema):
    profile_id: UUID
    kind: EvidenceKind
    text: str
    source: str | None = None
    origin: EvidenceOrigin = EvidenceOrigin.USER


class EvidenceCreate(EvidenceBase):
    pass


class EvidenceUpdate(Schema):
    # No `profile_id`: a claim cannot be moved to another person's vault.
    kind: EvidenceKind | None = None
    text: str | None = None
    source: str | None = None
    origin: EvidenceOrigin | None = None


class EvidenceRead(EvidenceBase):
    id: UUID
    created_at: datetime
