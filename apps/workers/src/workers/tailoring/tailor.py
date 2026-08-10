"""The stage: one `discovered` match in, two stored documents out — or one loud block.

Order is not arrangeable. Generate, **validate, then render**: Part 13 rule 2 says no
generated text reaches a PDF without passing the validator, and a validator that runs
after rendering is auditing rather than preventing.

Idempotency is the state machine, not a new column (§3.4). The stage acts on
`status='discovered'` and moves the match to `tailored` in the same transaction as the
document rows, with a conditional `UPDATE ... RETURNING` that yields no row if the status
moved underneath it. A retry after commit finds
`tailored` and does nothing; a worker killed mid-run rolls back whole, leaving a match
that will simply be picked up again. Nothing to check-then-act, and no second application
of anything.

A block is not a failure. It writes no document, leaves the match `discovered`, and says
why on the event — which means the next run tries again, which is right: the model is
sampled, and the same match may ground perfectly on the next attempt.

**The two documents are judged separately, and only the résumé decides whether anything
ships.** M5's third live gate blocked 7 of 20 honest pairs because a cover-letter paragraph
used a word like `offer` or `background`, and each of those discarded a résumé that had
validated cleanly. Nothing false shipped in any of them — the validator worked — so
throwing away the good document was punishing the wrong thing. A letter that cannot be
grounded is simply not written; the match still moves to `tailored` with its résumé, and
the letter block is recorded so M6 can ask for one again.
"""

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import storage
from db.events import record
from db.models import Document, Match
from schemas.enums import DocumentType, EvidenceKind, MatchStatus
from schemas.tailoring import CoverLetterDraft, TailoredResume
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from storage import drive

from workers import llm
from workers.tailoring import keywords, prompt, render, select, validate

# The first version of any document for a match. Versioning beyond this needs a reason to
# re-tailor, which is the deferred résumé-versioning item, so it is a constant not a knob.
FIRST_VERSION = 1

PDF = "application/pdf"


@dataclass(frozen=True)
class TailorResult:
    match_id: str
    # The résumé's verdict, and the one that decides whether anything ships.
    blocked: bool
    reason: str | None
    # The letter's, kept separate. A letter that cannot be grounded is a letter that is
    # not written; it is not a reason to discard a résumé that validated.
    letter_blocked: bool
    letter_reason: str | None
    bullets_returned: int
    bullets_kept: int
    bullets_stripped: int
    skills_kept: int
    fabricated_skills: int
    letter_paragraphs: int
    letter_rejected: int
    prompt_tokens: int
    completion_tokens: int


def tailor_match(
    session: Session,
    match_id: str,
    *,
    model: str,
    strip_ceiling: float,
    min_bullets: int,
) -> TailorResult | None:
    """Tailor one match. Returns `None` when there was nothing to do.

    Commits nothing — the caller owns the transaction, like every other stage function.
    """
    started = time.monotonic()
    work = select.load(session, uuid.UUID(match_id))
    if work is None or work.match.status != MatchStatus.DISCOVERED.value:
        return None

    claims = prompt.relevant(work.claims)
    usage: list[llm.Usage] = []

    drafted = llm.complete_json(
        TailoredResume,
        system=prompt.RESUME_SYSTEM,
        user=prompt.build_resume(
            claims=claims,
            title=work.job.title,
            company=work.job.company,
            description=work.job.description,
        ),
        usage=usage,
        model=model,
    )
    letter = llm.complete_json(
        CoverLetterDraft,
        system=prompt.LETTER_SYSTEM,
        user=prompt.build_letter(
            claims=prompt.for_letter(claims),
            title=work.job.title,
            company=work.job.company,
            description=work.job.description,
        ),
        usage=usage,
        model=model,
    )

    # ---- the guardrail, before anything is rendered ---------------------------------
    report = validate.resume(
        bullets=drafted.bullets,
        skills=drafted.skills,
        vault=work.vault,
        company=work.job.company or "",
        title=work.job.title or "",
    )
    call = validate.verdict(
        report,
        strip_ceiling=strip_ceiling,
        min_bullets=min_bullets,
        available_bullets=sum(
            1 for claim in work.vault.claims.values() if claim.kind == EvidenceKind.BULLET.value
        ),
    )
    letter_report = validate.cover_letter(
        paragraphs=letter.paragraphs,
        vault=work.vault,
        company=work.job.company,
        title=work.job.title,
    )

    result = TailorResult(
        match_id=match_id,
        blocked=call.blocked,
        reason=call.reason,
        letter_blocked=letter_report.blocked,
        letter_reason=_letter_reason(letter_report),
        bullets_returned=len(drafted.bullets),
        bullets_kept=len(report.kept),
        bullets_stripped=len(report.stripped),
        skills_kept=len(report.skills),
        fabricated_skills=len(report.fabricated_skills),
        letter_paragraphs=len(letter_report.paragraphs),
        letter_rejected=len(letter_report.rejected),
        prompt_tokens=sum(entry.prompt_tokens for entry in usage),
        completion_tokens=sum(entry.completion_tokens for entry in usage),
    )

    if result.blocked:
        # Every stripped bullet's reason goes on the event. §3.7's alert-on-volume applied
        # to text rather than to rows: a block with a count and no reasons is a number
        # nobody can act on, and this is the only record that the model tried.
        record(
            session,
            "tailor.blocked",
            {
                **asdict(result),
                "model": model,
                "stripped": [asdict(item) for item in report.stripped][:20],
                "letter_rejected_reasons": [item.reason for item in letter_report.rejected],
            },
            user_id=work.match.user_id,
        )
        return result

    # ---- render, store, mirror, record ----------------------------------------------
    resume_pdf = render.resume_pdf(
        resume=work.resume, bullets=report.kept, skills=report.skills, vault=work.vault
    )
    written = [_store(session, work.match, DocumentType.RESUME, resume_pdf)]

    letter_bytes = 0
    if not result.letter_blocked:
        letter_pdf = render.cover_letter_pdf(
            name=work.resume.basics.name or "Candidate",
            company=work.job.company,
            title=work.job.title,
            paragraphs=[paragraph.text for paragraph in letter_report.paragraphs],
            today=datetime.now(UTC).strftime("%d %B %Y"),
        )
        letter_bytes = len(letter_pdf)
        written.append(_store(session, work.match, DocumentType.COVER_LETTER, letter_pdf))

    moved = session.scalar(
        update(Match)
        .where(Match.id == work.match.id, Match.status == MatchStatus.DISCOVERED.value)
        .values(status=MatchStatus.TAILORED.value)
        .returning(Match.id)
    )
    if moved is None:
        # Someone else tailored this match between load and commit. The rollback is the
        # caller's; saying so beats a silent duplicate.
        raise RuntimeError(f"match {match_id} left 'discovered' mid-tailor")

    record(
        session,
        "tailor.generated",
        {
            **asdict(result),
            "model": model,
            "documents": written,
            "resume_bytes": len(resume_pdf),
            # What an ATS will actually see: of the candidate's own skills this posting
            # names, how many reached the document. Reported, never gated — a low number
            # is a selection to argue with, and the one thing it must never read as is a
            # suggestion to add something the vault does not hold.
            **keywords.coverage(
                claims=[
                    claim.text
                    for claim in work.vault.claims.values()
                    if claim.kind == EvidenceKind.SKILL.value
                ],
                description=work.job.description or "",
                rendered=" ".join([*report.skills, *(bullet.text for bullet in report.kept)]),
            ),
            # Zero when the letter did not ship. §3.7's alert-on-volume: a letter block
            # rate that climbs is invisible in an error rate, because nothing errored.
            "letter_bytes": letter_bytes,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
        user_id=work.match.user_id,
    )
    session.flush()
    return result


def _store(session: Session, match: Match, kind: DocumentType, pdf: bytes) -> str:
    """Put the PDF in the bucket, mirror it, then write the row that points at both.

    Blob first: a row pointing at an object that does not exist is a broken link M6 will
    send to a human, while an object with no row is garbage a lifecycle rule reaps.

    `on_conflict_do_nothing` on (match_id, type, version) so the database refuses a
    duplicate rather than a prior SELECT deciding there is none — Part 13 rule 10.
    """
    key = storage.build_document_key(match.id, kind.value, FIRST_VERSION, ".pdf")
    storage.put(key, pdf, PDF)

    session.execute(
        insert(Document)
        .values(
            match_id=match.id,
            type=kind.value,
            storage_url=key,
            gdrive_url=_mirror(session, match, kind, pdf),
            version=FIRST_VERSION,
        )
        .on_conflict_do_nothing(index_elements=[Document.match_id, Document.type, Document.version])
    )
    return key


def _mirror(session: Session, match: Match, kind: DocumentType, pdf: bytes) -> str | None:
    """Copy the PDF into the user's Drive folder. `None` when it did not happen.

    **A mirror failure never fails the stage.** R2 holds the durable copy and the model
    call — the expensive, non-repeatable part — has already succeeded. Raising here would
    throw away a valid document because a third party had a bad afternoon, and the next
    run would re-spend the tokens to produce the same bytes.

    It is recorded rather than logged, because a mirror that has quietly stopped working
    is exactly §3.7's shape: no exception, no error rate, just a column that is NULL more
    often than it used to be.
    """
    if not drive.is_configured():
        return None
    try:
        return drive.upload(
            name=f"{match.id}-{kind.value}.pdf",
            data=pdf,
            content_type=PDF,
        )
    except drive.DriveError as error:
        record(
            session,
            "tailor.mirror_failed",
            {"match_id": str(match.id), "type": kind.value, "error": str(error)[:200]},
            user_id=match.user_id,
        )
        return None


def _letter_reason(report: validate.LetterReport) -> str | None:
    """Every rejected paragraph's fault, not just the first one's.

    Reporting only `rejected[0]` made the letter-block rate unreadable: run 6 wrote no
    letter on 19 of 19 honest pairs and could name one word for it. One fault per rejected
    paragraph is a sample worth grouping across a run; one per *letter* is an anecdote.

    Still one fault per paragraph — `_untraceable` returns on its first — which is enough
    to see which words dominate without touching the rule the résumé path shares.

    Reporting only. The verdict is `LetterReport.blocked`, which is `bool(rejected)` and is
    not read from here.
    """
    if not report.rejected:
        return None
    return "cover letter: " + "; ".join(item.reason for item in report.rejected)
