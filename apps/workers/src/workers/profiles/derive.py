"""Pure functions from a `ParsedResume` to the values M4 filters on.

Nothing here calls a model, touches the network, or opens a session. That is the point:
`profiles.seniority`, `profiles.locations` and `profiles.work_auth` go into a `WHERE`
clause, and a filter input that varies between two runs over the same résumé silently
drops a different set of jobs each time. The model reports what is written on the page;
this file does the judging, with tests.
"""

import re
from datetime import UTC, date, datetime

from schemas.enums import Seniority, WorkAuth
from schemas.resume import ParsedResume, ResumeWork

from workers import seniority as bands

# Fallback when the title says nothing — "Software Engineer" carries no band. Years of
# experience, ascending; the first bound not exceeded wins.
_YEAR_BANDS: list[tuple[float, Seniority]] = [
    (2.0, Seniority.JUNIOR),
    (5.0, Seniority.MID),
    (9.0, Seniority.SENIOR),
]
_YEAR_BANDS_ABOVE = Seniority.STAFF

# Phrases a résumé actually uses, mapped to the enum.
#
# **Order is load-bearing, and it is not the obvious one.** A résumé says things like
# "EU citizen. Requires H-1B sponsorship for roles in the United States" — both a status
# and a need, in one sentence. Matching status first returns CITIZEN and hides the
# sponsorship requirement, which is the *dangerous* direction: §7.2 calls the work-auth
# filter the single most-praised feature in the leading product, and its entire value is
# not showing someone jobs they cannot legally take. The reverse error merely narrows
# their results.
#
# So: whether sponsorship is needed dominates what the person's status is. Negation
# comes before the need, because "does not require sponsorship" contains "require
# sponsorship" and would otherwise read as its own opposite.
#
# ponytail: "US citizen, no sponsorship required" resolves to VISA_HOLDER rather than
# CITIZEN. Both mean "can work here", so the filter behaves identically; the enum
# conflates status with need and untangling it is a schema change, not a reordering.
_WORK_AUTH_PHRASES: list[tuple[WorkAuth, tuple[str, ...]]] = [
    (
        # Sponsorship explicitly NOT needed. Must precede the block below.
        WorkAuth.VISA_HOLDER,
        (
            "no sponsorship required",
            "without sponsorship",
            "not require sponsorship",
            "does not require sponsorship",
            "authorized to work",
            "authorised to work",
            "work permit",
            "h-1b holder",
            "h1b holder",
            "ead",
        ),
    ),
    (
        # Sponsorship needed. Beats any status claimed in the same sentence.
        WorkAuth.NEEDS_SPONSORSHIP,
        (
            "require sponsorship",
            "requires sponsorship",
            "requiring sponsorship",
            "need sponsorship",
            "needs sponsorship",
            "sponsorship required",
            "seeking sponsorship",
            "h-1b sponsorship",
            "h1b sponsorship",
            "visa sponsorship",
        ),
    ),
    (
        WorkAuth.PERMANENT_RESIDENT,
        ("permanent resident", "green card", "greencard", "indefinite leave"),
    ),
    (
        WorkAuth.CITIZEN,
        ("citizen", "citizenship", "us national", "u.s. national"),
    ),
]

_MONTHS_PER_YEAR = 12


def years_of_experience(resume: ParsedResume, *, today: date | None = None) -> float | None:
    """Total months of employment, overlaps counted once, expressed in years.

    Merged rather than summed. Two concurrent roles — a job and a contract, a promotion
    recorded as two entries — are one stretch of a life, and summing them would report
    someone with eight years as having twelve. The published work-experience calculators
    all merge for exactly this reason.

    Returns None when no role carries a start date, because 0.0 would read as "no
    experience" rather than "we could not tell".
    """
    spans = [span for span in (_span(role, today) for role in resume.work) if span is not None]
    if not spans:
        return None

    ordered = sorted(spans)
    months = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        months += current_end - current_start
        current_start, current_end = start, end
    months += current_end - current_start

    return round(months / _MONTHS_PER_YEAR, 1)


def seniority(resume: ParsedResume, years: float | None) -> Seniority | None:
    """The most recent title's band, or the years band when that title is silent.

    Title first because it is a stated fact and tenure is a proxy: someone promoted to
    Staff at six years is Staff, and a career-changer with fifteen years in another
    field is not.

    **Only the most recent role's title is consulted.** Scanning further back reads a
    band off a job the person has left: a résumé whose current role is "Backend
    Engineer" and whose first job was "Junior Developer" resolves to JUNIOR, six years
    after they stopped being one. `_by_recency` already puts the current role first, so
    scanning past it buys nothing and costs that. Found by the live gate on plain.txt.

    A bandless current title falls through to tenure, which is the right answer for the
    very common "Software Engineer" with no adjective.
    """
    current = next(iter(_by_recency(resume.work)), None)
    if current is not None:
        band = bands.band(current.position)
        if band is not None:
            return band
    if years is None:
        return None
    for bound, band in _YEAR_BANDS:
        if years < bound:
            return band
    return _YEAR_BANDS_ABOVE


def locations(resume: ParsedResume) -> list[str]:
    """The one place the résumé names, as the résumé spells it.

    Deliberately **not** normalized, because `jobs.locations` is not either — normalizing
    one side of a comparison makes it worse, not better.

    M4 does not compare the two arrays directly: measured on the open pool, an exact
    overlap matched 141 rows for "San Francisco, CA" where a case-folded segment match
    found 808. `matching/filters.py` splits both sides on commas and folds case; what
    matters here is only that this stays the string the résumé used.

    A list because the column is one, and because M4's filter is really "where would
    this person work" — which the user extends through the API. The parse only ever
    seeds it.
    """
    place = resume.basics.location
    if place is None:
        return []
    parts = [part for part in (place.city, place.region) if part]
    return [", ".join(parts)] if parts else []


def work_auth(resume: ParsedResume) -> WorkAuth | None:
    """Map the phrase the résumé used, or None.

    None is the common case and the correct one: most résumés never mention it. §7.2
    makes the work-auth filter M4's job, and DECISIONS.md's `is_remote=False` entry sets
    the polarity — unknown must never be treated as a failed filter.
    """
    stated = (resume.work_authorization or "").lower()
    if not stated:
        return None
    for auth, phrases in _WORK_AUTH_PHRASES:
        if any(phrase in stated for phrase in phrases):
            return auth
    return None


def _span(role: ResumeWork, today: date | None) -> tuple[int, int] | None:
    """One role as (start, end) in absolute months. None when undatable."""
    start = _months(role.start_date)
    if start is None:
        # No start date means no span. Guessing one from the end date would invent
        # tenure, which is the exact failure mode this whole stage guards against.
        return None
    now = today or datetime.now(UTC).date()
    end = _months(role.end_date, last=True) or (now.year * _MONTHS_PER_YEAR + now.month)
    # A résumé with a typo can end before it starts. Treat it as a point, not a negative.
    return (start, max(start, end))


def _months(value: str | None, *, last: bool = False) -> int | None:
    """`YYYY-MM` or `YYYY` to an absolute month number.

    Ends are exclusive, which is what makes "2020-01 to 2023-01" read as 36 months
    rather than 37. A bare year therefore ends at the *following* January: "2020" to
    "2021" is two full years of work, and resolving both to January would report it as
    one month.

    The pattern check is belt-and-braces — `ResumeDate` already constrains the schema —
    but `master_resume` text can also reach here through a hand-seeded profile.
    """
    if not value:
        return None
    if not re.fullmatch(r"\d{4}(-\d{2})?", value):
        return None
    year, _, month = value.partition("-")
    if month:
        return int(year) * _MONTHS_PER_YEAR + int(month)
    return int(year) * _MONTHS_PER_YEAR + (_MONTHS_PER_YEAR + 1 if last else 1)


def _by_recency(roles: list[ResumeWork]) -> list[ResumeWork]:
    """Newest first. A role still open sorts above every finished one."""
    return sorted(
        roles,
        key=lambda role: (
            role.end_date is None and role.start_date is not None,
            _months(role.end_date, last=True) or _months(role.start_date) or 0,
        ),
        reverse=True,
    )
