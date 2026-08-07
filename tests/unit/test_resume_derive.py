"""The deterministic half of M3.

Everything here is a pure function over a `ParsedResume`. That is the point of the
split: `profiles.seniority`, `profiles.locations` and `profiles.work_auth` go into M4's
`WHERE` clause, and a filter input that varied between two runs over one résumé would
drop a different set of jobs each time. So the model reports what is on the page and
this file does the judging, testably, with no network.
"""

from datetime import date

import pytest
from pydantic import ValidationError
from schemas.enums import Seniority, WorkAuth
from schemas.resume import (
    ParsedResume,
    ResumeBasics,
    ResumeLocation,
    ResumeWork,
)
from workers.profiles import derive

TODAY = date(2026, 8, 7)


def _resume(*roles: ResumeWork, **kwargs: object) -> ParsedResume:
    return ParsedResume(work=list(roles), **kwargs)  # type: ignore[arg-type]


def _role(start: str | None, end: str | None, position: str | None = None) -> ResumeWork:
    return ResumeWork(position=position, start_date=start, end_date=end)


# ------------------------------------------------------------- years of experience


def test_a_single_closed_role_counts_its_own_span() -> None:
    resume = _resume(_role("2020-01", "2023-01"))

    assert derive.years_of_experience(resume, today=TODAY) == 3.0


def test_an_open_role_counts_up_to_today() -> None:
    """A null end date means "still there" — that is the whole reason the dates are
    strings rather than `date`."""
    resume = _resume(_role("2024-08", None))

    assert derive.years_of_experience(resume, today=TODAY) == 2.0


def test_overlapping_roles_are_counted_once() -> None:
    """The reason this is a merge and not a sum. A job and a concurrent contract are one
    stretch of a life; summing them reports eight years as twelve."""
    resume = _resume(_role("2018-01", "2022-01"), _role("2020-01", "2024-01"))

    assert derive.years_of_experience(resume, today=TODAY) == 6.0


def test_a_role_fully_inside_another_adds_nothing() -> None:
    resume = _resume(_role("2018-01", "2026-01"), _role("2020-01", "2021-01"))

    assert derive.years_of_experience(resume, today=TODAY) == 8.0


def test_a_gap_between_roles_is_not_counted() -> None:
    """A career break is not experience. Merging must not bridge the hole."""
    resume = _resume(_role("2014-01", "2016-01"), _role("2020-01", "2022-01"))

    assert derive.years_of_experience(resume, today=TODAY) == 4.0


def test_roles_out_of_order_merge_the_same() -> None:
    """A résumé lists newest first. The merge sorts; if it did not, it would read the
    first entry as the anchor and mis-total every résumé in the world."""
    newest_first = _resume(_role("2020-01", "2024-01"), _role("2018-01", "2022-01"))
    oldest_first = _resume(_role("2018-01", "2022-01"), _role("2020-01", "2024-01"))

    assert derive.years_of_experience(newest_first, today=TODAY) == derive.years_of_experience(
        oldest_first, today=TODAY
    )


def test_a_year_only_range_spans_the_whole_year() -> None:
    """ "2020" to "2021" is two years of work, not one month. Treating a bare year as
    January on both ends erases up to eleven months per entry."""
    resume = _resume(_role("2020", "2021"))

    assert derive.years_of_experience(resume, today=TODAY) == 2.0


def test_a_role_with_no_start_date_is_skipped_not_guessed() -> None:
    """Inventing a start from the end date would invent tenure, which is the exact
    failure mode this stage exists to prevent."""
    resume = _resume(_role(None, "2022-01"))

    assert derive.years_of_experience(resume, today=TODAY) is None


def test_no_dates_at_all_is_unknown_not_zero() -> None:
    """None reads as "we could not tell"; 0.0 would read as "no experience" and put a
    junior band on a principal engineer."""
    assert derive.years_of_experience(_resume(_role(None, None)), today=TODAY) is None
    assert derive.years_of_experience(_resume(), today=TODAY) is None


def test_a_backwards_range_does_not_subtract() -> None:
    """A typo'd résumé must not produce negative experience."""
    resume = _resume(_role("2024-01", "2020-01"))

    assert derive.years_of_experience(resume, today=TODAY) == 0.0


def test_a_malformed_date_never_reaches_the_arithmetic() -> None:
    """The schema is the first guard: `ResumeDate` refuses anything but YYYY[-MM], so a
    model that answers "last year" trips the LLM client's corrective retry rather than
    silently landing in 1970. Verified at both layers, because a hand-seeded profile can
    reach `derive` without passing through the schema."""
    with pytest.raises(ValidationError):
        _role("last year", "2024-01")

    assert derive._months("last year") is None


# ------------------------------------------------------------------------ seniority


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Software Engineer", Seniority.SENIOR),
        ("Sr. Backend Engineer", Seniority.SENIOR),
        ("Staff Engineer", Seniority.STAFF),
        ("Principal Engineer", Seniority.PRINCIPAL),
        ("Engineering Manager", Seniority.LEAD),
        ("Tech Lead", Seniority.LEAD),
        ("Director of Engineering", Seniority.DIRECTOR),
        ("VP of Engineering", Seniority.DIRECTOR),
        ("Software Engineering Intern", Seniority.INTERN),
        ("Junior Developer", Seniority.JUNIOR),
        ("Associate Data Scientist", Seniority.JUNIOR),
        ("Solutions Architect", Seniority.STAFF),
    ],
)
def test_the_title_decides_the_band(title: str, expected: Seniority) -> None:
    resume = _resume(_role("2020-01", None, title))

    assert derive.seniority(resume, 6.0) is expected


def test_the_most_senior_keyword_wins_within_one_title() -> None:
    """ "Senior Staff Engineer" is Staff. Scanning in ascending order would call it
    Senior and under-band the person on every match."""
    resume = _resume(_role("2020-01", None, "Senior Staff Engineer"))

    assert derive.seniority(resume, 6.0) is Seniority.STAFF


def test_a_bare_title_falls_back_to_tenure() -> None:
    """ "Software Engineer" carries no band at all, and most résumés are full of them."""
    resume = _resume(_role("2020-01", None, "Software Engineer"))

    assert derive.seniority(resume, 6.0) is Seniority.SENIOR
    assert derive.seniority(resume, 1.0) is Seniority.JUNIOR
    assert derive.seniority(resume, 3.0) is Seniority.MID
    assert derive.seniority(resume, 12.0) is Seniority.STAFF


def test_the_title_beats_tenure() -> None:
    """Promotion is a stated fact; tenure is a proxy. Someone made Staff at six years is
    Staff, and a career-changer with fifteen years elsewhere is not."""
    resume = _resume(_role("2020-01", None, "Staff Engineer"))

    assert derive.seniority(resume, 6.0) is Seniority.STAFF


def test_a_title_on_an_older_role_still_counts() -> None:
    """Titles are scanned across every role, so a résumé that lists its current job last
    still resolves rather than falling through to tenure."""
    resume = _resume(_role("2015-01", "2018-01", "Software Engineer"))
    resume = _resume(*resume.work, _role("2018-01", None, "Principal Engineer"))

    assert derive.seniority(resume, 10.0) is Seniority.PRINCIPAL


def test_no_title_and_no_tenure_is_none() -> None:
    assert derive.seniority(_resume(), None) is None


# ------------------------------------------------------------------------ locations


def test_the_location_is_kept_as_the_resume_spells_it() -> None:
    """Deliberately NOT normalized. `profiles.locations` is matched against
    `jobs.locations` with `&&`, and `jobs.locations` holds each source's own strings —
    normalizing one side of an overlap makes matching worse, not better."""
    resume = ParsedResume(
        basics=ResumeBasics(location=ResumeLocation(city="San Francisco", region="CA"))
    )

    assert derive.locations(resume) == ["San Francisco, CA"]


def test_a_city_with_no_region_is_still_a_location() -> None:
    resume = ParsedResume(basics=ResumeBasics(location=ResumeLocation(city="Berlin")))

    assert derive.locations(resume) == ["Berlin"]


def test_no_location_yields_an_empty_list_not_a_blank_string() -> None:
    """An empty string in a TEXT[] would match nothing and look like a value."""
    assert derive.locations(ParsedResume()) == []
    assert derive.locations(ParsedResume(basics=ResumeBasics(location=ResumeLocation()))) == []


# ------------------------------------------------------------------------ work auth


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("US citizen", WorkAuth.CITIZEN),
        ("Green Card holder", WorkAuth.PERMANENT_RESIDENT),
        ("Permanent resident of Canada", WorkAuth.PERMANENT_RESIDENT),
        ("Authorized to work in the US without sponsorship", WorkAuth.VISA_HOLDER),
        ("Requires H-1B sponsorship", WorkAuth.NEEDS_SPONSORSHIP),
        ("Will need visa sponsorship", WorkAuth.NEEDS_SPONSORSHIP),
    ],
)
def test_a_stated_authorisation_maps_to_the_enum(stated: str, expected: WorkAuth) -> None:
    assert derive.work_auth(ParsedResume(work_authorization=stated)) is expected


def test_not_requiring_sponsorship_is_not_needing_sponsorship() -> None:
    """The phrase contains the word. Order in the mapping is what stops "does not
    require sponsorship" reading as its own opposite — which would hide every eligible
    job from someone already authorised."""
    resume = ParsedResume(work_authorization="Does not require sponsorship")

    assert derive.work_auth(resume) is WorkAuth.VISA_HOLDER


def test_an_unstated_authorisation_is_none_not_a_guess() -> None:
    """The common case: most résumés never mention it. NULL means unknown, and §7.2
    makes the filter M4's job — DECISIONS.md's is_remote=False entry sets the polarity,
    unknown must never read as a failed filter."""
    assert derive.work_auth(ParsedResume()) is None
    assert derive.work_auth(ParsedResume(work_authorization="")) is None


def test_an_unrecognised_phrase_is_none_rather_than_the_nearest_match() -> None:
    assert derive.work_auth(ParsedResume(work_authorization="open to relocation")) is None
