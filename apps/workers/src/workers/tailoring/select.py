"""Everything one tailoring call needs, read as rows.

§3.1: this is where M5 touches M3's and M4's work, and it touches it through the
database. `evidence` rows, `profiles.parsed_json`, the `jobs` row and the `matches` row —
no import reaches into `workers.profiles` or `workers.matching`, and none is needed,
because the vault is a table.

The handles (`E7`) are minted here, in a stable order: evidence rows are read sorted by
`created_at, id`, which is insertion order, which is the order `vault.claims` walked the
résumé in. The prompt sends handles, the model echoes one back per bullet, and the
validator resolves them. Nothing outside this stage ever sees a handle — it is a
per-call label, not an identifier — which is why it can be short.
"""

import uuid
from dataclasses import dataclass

from db.models import Evidence, Job, Match, Profile
from schemas.resume import ParsedResume
from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from workers.tailoring.validate import Claim, Vault


@dataclass(frozen=True)
class Work:
    """One match's world. Everything downstream is a pure function of this."""

    match: Match
    job: Job
    profile: Profile
    resume: ParsedResume
    vault: Vault
    # The vault rows in prompt order, so `prompt.py` and the validator agree on handles
    # without either of them owning the numbering.
    claims: list[Claim]

    def claim_for(self, handle: str) -> Claim | None:
        return self.vault.claims.get(handle)


def load(session: Session, match_id: uuid.UUID) -> Work | None:
    """Assemble one match's world, or `None` if it is not tailorable.

    `None` rather than an exception for the ordinary cases — a match already tailored, a
    profile that never parsed — because those are states, not failures, and the caller
    logs them as skips. A missing row is the same: deleted between fan-out and execution
    is not an error, which is the rule `tasks/matching.py` already set.
    """
    match = session.get(Match, match_id)
    if match is None:
        return None

    job = session.get(Job, match.job_id)
    profile = session.scalar(sql_select(Profile).where(Profile.user_id == match.user_id))
    if job is None or profile is None or not profile.parsed_json:
        return None

    resume = ParsedResume.model_validate(profile.parsed_json)
    rows = list(
        session.scalars(
            sql_select(Evidence)
            .where(Evidence.profile_id == profile.id)
            .order_by(Evidence.created_at, Evidence.id)
        )
    )
    claims = [
        Claim(id=f"E{index + 1}", kind=row.kind, text=row.text, source=row.source or "")
        for index, row in enumerate(rows)
    ]

    vault = Vault(
        claims={claim.id: claim for claim in claims},
        companies=tuple(entry.name for entry in resume.work if entry.name),
        titles=tuple(entry.position for entry in resume.work if entry.position),
    )
    return Work(match=match, job=job, profile=profile, resume=resume, vault=vault, claims=claims)
