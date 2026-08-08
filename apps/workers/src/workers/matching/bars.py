"""Disqualifiers a `WHERE` clause could find, computed here instead of asked of a model.

**Why this is separate from `MatchFacts.disqualifiers`.** Both zero a score; they differ
in who decides and what that costs. The model reads a posting and quotes a refusal — real
comprehension, and the only way to catch "unable to consider applications from candidates
in other time zones". These three checks need no comprehension at all, and asking for
them measurably made the model worse: extraction was 8/8 when the prompt was responsible
only for clauses that must be read, and 9/13 after two more categories were added to the
same rule. Attention is finite and a prompt is not a list.

They also fail differently. A model miss is invisible and moves between runs; a bug here
fails a unit test the same way every time. §3.5's ordering applied one rung lower: never
ask a model for something a pure function can decide.

**Not hard filters.** These are irreversible in a `WHERE` clause and merely wrong in a
score. A filter that drops a real match removes it before anything can measure the
mistake; a score of 0 is visible in the golden set as a lost positive. BAR.md floors
filter recall at 0.90 for that reason, and none of these three is confident enough to
spend that budget.

Every check keeps `filters.py`'s polarity: **silence passes.** An unmappable location, an
unstated language and an absent eligibility clause all yield nothing.
"""

import re

from db.models import Job
from schemas.profile import ProfileRead

from workers import regions

# Languages a posting can require, and what the profile must show to satisfy it. Only
# languages the pool actually demands — roughly a quarter of it is German-language, and
# the fixtures met Mandarin and French.
_LANGUAGE_DEMANDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Mandarin",
        r"bilingual [^.]{0,30}mandarin|mandarin (?:is )?required",
        ("mandarin", "chinese"),
    ),
    ("German", r"fluent in german|flie\wend|deutschkenntnisse in wort", ("german", "deutsch")),
    ("French", r"fluent in french|couramment (?:le )?fran\wais", ("french", "français")),
)

# A programme open only to people at a career stage. The profile fails it by being past
# that stage, which no amount of skill overlap offsets — and these postings read as an
# excellent match, because the craft really is the same.
_ELIGIBILITY = re.compile(
    r"current university students and recent graduates"
    r"|open (?:only )?to (?:current )?students"
    r"|must be currently enrolled",
    re.I,
)

# Bands that are not an early-career intake. A profile at or above these fails an
# eligibility window; a junior or a student does not.
_SENIOR_BANDS = frozenset({"mid", "senior", "staff", "principal", "lead"})


def check(job: Job, profile: ProfileRead, resume: str) -> list[str]:
    """Every deterministic bar this pair trips. Empty is the common case."""
    found = [
        _country_scope(job, profile),
        _language(job, resume),
        _eligibility(job, profile),
    ]
    return [bar for bar in found if bar]


def _country_scope(job: Job, profile: ProfileRead) -> str | None:
    """A role scoped to somewhere the profile may not legally work.

    The case no quoted span can catch: "Remote — United States" states a location, not a
    refusal, so there is nothing in the posting for a model to quote. It is still a job
    the candidate cannot take, and BAR.md §6 rule 3's stated purpose — not showing
    someone jobs they cannot legally take — does not stop at postings that spell it out.

    Silence passes three ways, because each is a different kind of not-knowing: the
    profile never stated its authorisation, the posting named no place we recognise, or
    the posting declined to name one at all.
    """
    if profile.work_auth_regions is None:
        return None
    places = list(job.locations or []) + ([job.location] if job.location else [])
    if not places:
        return None

    # **Named regions win over the global phrases.** "Remote, United States" contains the
    # word remote and is still scoped to one country; checking globality first would read
    # every US remote posting as open to the world, which is the single most common
    # location string in the pool.
    joined = " ".join(places)
    wanted = regions.named_in(joined)
    if not wanted or regions.is_open_to_the_world(joined, wanted):
        return None
    authorised = set(profile.work_auth_regions)
    if regions.covers(authorised, wanted):
        return None
    return (
        f"role is based in {'/'.join(sorted(wanted))}; "
        f"profile is authorised to work in {'/'.join(sorted(authorised)) or 'nowhere stated'}"
    )


def _language(job: Job, resume: str) -> str | None:
    """A working language the posting requires and the résumé never mentions.

    Only fires on an explicit demand — "Bilingual English/Mandarin is required", not a
    posting that happens to be written in German. The résumé is searched for the language
    by name, which is how a candidate who speaks it says so.
    """
    haystack = f"{job.title} {job.description or ''}"
    lowered = resume.lower()
    for name, demand, evidence in _LANGUAGE_DEMANDS:
        if re.search(demand, haystack, re.I) and not any(word in lowered for word in evidence):
            return f"posting requires {name}; the résumé does not mention it"
    return None


def _eligibility(job: Job, profile: ProfileRead) -> str | None:
    """An intake open only to students or recent graduates, against someone past that."""
    if (profile.seniority or "").lower() not in _SENIOR_BANDS:
        return None
    match = _ELIGIBILITY.search(job.description or "")
    if match is None:
        return None
    return f"programme is limited to {match.group(0).lower()}; profile is {profile.seniority}"
