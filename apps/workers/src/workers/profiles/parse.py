"""One profile in, a parsed record and a vault out. M3's gate.

A plain function over `(session, profile)` rather than a Celery task, for the same
reason `ingest.ingest_company` is one: Part 10's rule is seed the tables, run the stage,
assert the delta, and a task would put a broker between the test and the assertion.
Commits nothing.

The promotion rule is the one piece of policy here worth reading twice. A parse fills a
promoted column **only when it is currently empty**. A user who set their seniority or
their target locations by hand has said something the résumé cannot contradict, and a
re-upload must not silently overwrite it.

ponytail: the flip side is that a genuine career change leaves stale columns until the
user edits them. Accepted — the alternative is tracking which fields a user has touched,
which is real state for a case a profile edit already solves. Revisit if a real user
hits it.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

import storage
from db.events import record
from db.models import Profile
from schemas.resume import ParsedResume
from sqlalchemy.orm import Session

from workers import llm
from workers.profiles import derive, extract, prompt, vault


@dataclass(frozen=True)
class ParseResult:
    resume_chars: int
    skills: int
    roles: int
    claims_stored: int
    claims_rejected: int
    promoted: list[str]


def parse_profile(session: Session, profile: Profile) -> ParseResult:
    """Extract, structure, derive, and rebuild the vault. Commits nothing."""
    source = _resume_text(profile)
    profile.master_resume = source

    resume = llm.complete_json(ParsedResume, system=prompt.SYSTEM, user=prompt.build(source))
    years = derive.years_of_experience(resume)
    # Stored on the record rather than recomputed downstream: M4 reads parsed_json, and
    # a second implementation of the merge arithmetic is a second thing to get wrong.
    resume = resume.model_copy(update={"years_experience": years})

    profile.parsed_json = resume.model_dump(mode="json")
    promoted = _promote(profile, resume, years)

    result = vault.rebuild(session, profile.id, resume, source)
    session.flush()

    record(
        session,
        "profile.parsed",
        {
            "resume_chars": len(source),
            "skills": len(resume.skills),
            "roles": len(resume.work),
            "claims_stored": result.stored,
            # §3.7: alert on volume, not only on errors. A non-zero count here means the
            # model paraphrased instead of copying, which loses real evidence silently
            # unless somebody is counting it.
            "claims_rejected": result.rejected,
            "kept_user_claims": result.kept_user_claims,
            "promoted": promoted,
        },
        user_id=profile.user_id,
    )

    return ParseResult(
        resume_chars=len(source),
        skills=len(resume.skills),
        roles=len(resume.work),
        claims_stored=result.stored,
        claims_rejected=result.rejected,
        promoted=promoted,
    )


def _resume_text(profile: Profile) -> str:
    """The uploaded file's text, or the text already on the row.

    Both paths exist on purpose. An upload is the normal route; `master_resume` set
    directly is what a test, a seed, or a future paste-your-résumé box uses, and
    supporting it costs one branch.
    """
    if profile.resume_url:
        suffix = PurePosixPath(profile.resume_url).suffix
        return extract.to_markdown(storage.get(profile.resume_url), suffix)
    if profile.master_resume and profile.master_resume.strip():
        return profile.master_resume.strip()
    raise extract.ExtractError("profile has neither an uploaded résumé nor résumé text")


def _promote(profile: Profile, resume: ParsedResume, years: float | None) -> list[str]:
    """Fill the columns M4 filters on, without overwriting anything a user set.

    Returns the names actually written, so the event says what this run decided rather
    than what it looked at.
    """
    promoted: list[str] = []

    if profile.seniority is None:
        band = derive.seniority(resume, years)
        if band is not None:
            profile.seniority = band.value
            promoted.append("seniority")

    if profile.work_auth is None:
        auth = derive.work_auth(resume)
        if auth is not None:
            profile.work_auth = auth.value
            promoted.append("work_auth")

    if not profile.locations:
        places = derive.locations(resume)
        if places:
            profile.locations = places
            promoted.append("locations")

    # `salary_floor` is deliberately absent. A résumé does not state one, and inferring
    # it from a title would be exactly the invented value this stage exists to prevent.
    return promoted
