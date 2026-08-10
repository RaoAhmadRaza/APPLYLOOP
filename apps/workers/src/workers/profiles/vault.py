"""The evidence vault: atomic claims, each verified against the source text.

§3.3 says M5's validator diffs every generated bullet against the vault and strips
anything not traceable. That guarantee is only worth something if the vault itself is
true. If the *parser* invents a skill, M5 will find it, declare the bullet traceable,
and put a lie on somebody's résumé — the validator working perfectly and proving
nothing.

So the check happens here, at write time: **a claim is stored only if its text appears
in that profile's `master_resume`.** The model is instructed to copy rather than
summarise (see prompt.py) precisely so this rarely fires; when it does, the claim is
dropped and counted, never repaired.

Comparison is whitespace-and-case-insensitive over letters and digits. Anything
stricter fails on the newline markitdown inserted mid-bullet; anything looser (fuzzy
matching, embeddings) would admit a paraphrase, which is exactly the thing being
excluded. Same reasoning `dedupe.py` gives for refusing a similarity threshold.

Rebuild deletes and re-inserts only `origin='parsed'` rows. A claim a user added by
hand is theirs, and a re-upload must not take it.
"""

import uuid
from dataclasses import dataclass

from db.models import Evidence
from schemas.enums import EvidenceKind, EvidenceOrigin
from schemas.resume import ParsedResume
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from workers.text import split_skill_line, squash

# A claim shorter than this carries no information a validator could act on: a one-word
# bullet, a stray "C" that would match half the alphabet's worth of source text.
MIN_CLAIM_LENGTH = 2


@dataclass(frozen=True)
class Claim:
    kind: EvidenceKind
    text: str
    source: str


@dataclass(frozen=True)
class VaultResult:
    stored: int
    # Claims the model produced that are NOT in the résumé. Non-zero means the prompt
    # regressed or the model paraphrased; §3.7's "alert on volume" applies — this is
    # recorded on the event, not swallowed.
    rejected: int
    kept_user_claims: int


def claims(resume: ParsedResume) -> list[Claim]:
    """Every checkable assertion in a parsed résumé, as flat rows.

    Flat because M5 checks one generated bullet against one source. A nested structure
    would make "is this traceable?" a tree walk instead of a lookup.
    """
    found: list[Claim] = []

    for index, skill in enumerate(resume.skills):
        if skill.name:
            # `name` is supposed to be the group LABEL, with the members in `keywords`.
            # Whether the model obeys that is a property of the run and not of the
            # résumé: the same fixture parsed twice gave `{"Languages", [...]}` once and
            # `{"Languages: Python, Go, SQL, TypeScript", []}` the next time, and the
            # second shape buries four real skills in one claim that nothing can match.
            #
            # Splitting here rather than re-prompting because the prompt already asks for
            # the right shape (`profiles/prompt.py`) and asking harder is not a fix. The
            # whole line is kept as well: it is what the résumé says, and dropping it
            # would break a bullet that quotes the line verbatim.
            found.extend(
                Claim(EvidenceKind.SKILL, part, f"skills[{index}]")
                for part in dict.fromkeys([skill.name, *split_skill_line(skill.name)])
            )
        # Keywords are where the real skills usually are when the model obeys.
        # §3.3 names inventing a *skill* as the adversarial case, so each one has to be
        # individually checkable, not buried in a group label.
        found.extend(
            Claim(EvidenceKind.SKILL, keyword, f"skills[{index}].keywords[{position}]")
            for position, keyword in enumerate(skill.keywords)
            if keyword
        )

    for index, role in enumerate(resume.work):
        if role.position:
            found.append(Claim(EvidenceKind.TITLE, role.position, f"work[{index}].position"))
        found.extend(
            Claim(EvidenceKind.BULLET, highlight, f"work[{index}].highlights[{position}]")
            for position, highlight in enumerate(role.highlights)
            if highlight
        )

    for index, project in enumerate(resume.projects):
        found.extend(
            Claim(EvidenceKind.BULLET, highlight, f"projects[{index}].highlights[{position}]")
            for position, highlight in enumerate(project.highlights)
            if highlight
        )

    for index, degree in enumerate(resume.education):
        if degree.institution:
            found.append(Claim(EvidenceKind.CREDENTIAL, degree.institution, f"education[{index}]"))

    for index, certificate in enumerate(resume.certificates):
        if certificate.name:
            found.append(Claim(EvidenceKind.CREDENTIAL, certificate.name, f"certificates[{index}]"))

    return [claim for claim in found if len(claim.text.strip()) >= MIN_CLAIM_LENGTH]


def is_supported(claim_text: str, source_text: str) -> bool:
    """Does this claim actually appear in the résumé?

    The whole §3.3 guarantee, in one line of containment. Both sides are squashed to
    letters and digits first, so a line break markitdown introduced mid-sentence, a
    non-breaking space, or an accent rendered two ways cannot reject a true claim.

    `workers.text.squash` rather than a local copy: M5's validator runs the other half
    of this comparison and the two have to be the same transform, or the chain breaks
    at the join. See that module.
    """
    squashed = squash(claim_text)
    return bool(squashed) and squashed in squash(source_text)


def rebuild(
    session: Session, profile_id: uuid.UUID, resume: ParsedResume, source: str
) -> VaultResult:
    """Replace this profile's parsed claims with the ones this résumé supports.

    Delete-then-insert rather than a diff: a changed bullet is a *different* claim, not
    the same one with new text, so there is nothing to update. The unique constraint on
    (profile_id, kind, text) makes a repeat run land on the same rows either way.

    Commits nothing — the caller owns the transaction, like every other stage function.
    """
    kept_user_claims = session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(
            Evidence.profile_id == profile_id,
            Evidence.origin == EvidenceOrigin.USER.value,
        )
    )

    session.execute(
        delete(Evidence).where(
            Evidence.profile_id == profile_id,
            Evidence.origin == EvidenceOrigin.PARSED.value,
        )
    )

    proposed = claims(resume)
    supported = [claim for claim in proposed if is_supported(claim.text, source)]
    # Two claims can normalise to one row — a skill listed in a group and again on its
    # own. The unique constraint would refuse the second, so collapse first and let the
    # first occurrence keep its source pointer.
    unique: dict[tuple[EvidenceKind, str], Claim] = {}
    for claim in supported:
        unique.setdefault((claim.kind, claim.text), claim)

    session.add_all(
        Evidence(
            profile_id=profile_id,
            kind=claim.kind.value,
            text=claim.text,
            source=claim.source,
            origin=EvidenceOrigin.PARSED.value,
        )
        for claim in unique.values()
    )
    session.flush()

    return VaultResult(
        stored=len(unique),
        rejected=len(proposed) - len(supported),
        kept_user_claims=kept_user_claims or 0,
    )
