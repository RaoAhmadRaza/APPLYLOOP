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


def test_the_current_role_decides_even_when_it_is_listed_last() -> None:
    """`_by_recency` puts the open role first regardless of the order the résumé used."""
    resume = _resume(
        _role("2015-01", "2018-01", "Software Engineer"),
        _role("2018-01", None, "Principal Engineer"),
    )

    assert derive.seniority(resume, 10.0) is Seniority.PRINCIPAL


def test_an_old_title_never_bands_a_current_bandless_one() -> None:
    """**Found by the live gate on plain.txt.** A current "Backend Engineer" and a first
    job titled "Junior Developer" resolved to JUNIOR — six years after they stopped
    being one. Only the most recent title is consulted; a bandless one falls through to
    tenure, which is what eight years of it should say."""
    resume = _resume(
        _role("2020-02", None, "Backend Engineer"),
        _role("2018-07", "2020-01", "Junior Developer"),
    )

    assert derive.seniority(resume, 8.0) is Seniority.SENIOR


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


def test_needing_sponsorship_beats_a_status_stated_in_the_same_sentence() -> None:
    """**Found by the live gate on two_column.pdf.** "EU citizen. Requires H-1B
    sponsorship for roles based in the United States" resolved to CITIZEN, because
    status was matched before need — which is the dangerous direction: §7.2's whole
    point is not showing someone jobs they cannot legally take."""
    resume = ParsedResume(
        work_authorization="EU citizen. Requires H-1B sponsorship for roles in the United States"
    )

    assert derive.work_auth(resume) is WorkAuth.NEEDS_SPONSORSHIP


def test_a_bare_citizenship_claim_is_still_citizen() -> None:
    """The reordering must not swallow the simple case."""
    assert derive.work_auth(ParsedResume(work_authorization="UK citizen.")) is WorkAuth.CITIZEN


def test_an_unstated_authorisation_is_none_not_a_guess() -> None:
    """The common case: most résumés never mention it. NULL means unknown, and §7.2
    makes the filter M4's job — DECISIONS.md's is_remote=False entry sets the polarity,
    unknown must never read as a failed filter."""
    assert derive.work_auth(ParsedResume()) is None
    assert derive.work_auth(ParsedResume(work_authorization="")) is None


def test_an_unrecognised_phrase_is_none_rather_than_the_nearest_match() -> None:
    assert derive.work_auth(ParsedResume(work_authorization="open to relocation")) is None


# ---- work_auth_regions: WHERE the authorisation applies --------------------------


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        # The four golden fixtures, verbatim. These are the strings M4's gate runs on.
        ("UK citizen.", ["GB"]),
        ("Authorized to work in the United States without sponsorship.", ["US"]),
        (
            "EU citizen. Requires H-1B sponsorship for roles\nbased in the United States.",
            ["EU"],
        ),
        # Only a negative is stated. That is not knowledge of where they CAN work —
        # see the note in test_a_negative_only_statement_is_silence_not_nowhere.
        ("Requires visa sponsorship to work in the United Kingdom.", None),
    ],
)
def test_the_fixtures_resolve_to_the_regions_they_name(stated: str, expected: list[str]) -> None:
    assert derive.work_auth_regions(ParsedResume(work_authorization=stated)) == expected


def test_silence_is_none() -> None:
    """`None` means the résumé did not say, and never bars anything."""
    assert derive.work_auth_regions(ParsedResume()) is None
    assert derive.work_auth_regions(ParsedResume(work_authorization="")) is None


def test_a_negative_only_statement_is_silence_not_nowhere() -> None:
    """**The column holds where the candidate IS authorised.**

    "Requires visa sponsorship to work in the United Kingdom" excludes the UK and says
    nothing about Canada. An earlier version returned `[]` for this — read downstream as
    "authorised nowhere" — which barred every located job for that profile, including the
    single role the golden set labels them a good fit for. Caught by tracing the
    consequence onto a real pair before running it.
    """
    resume = ParsedResume(work_authorization="Requires visa sponsorship to work in the UK.")

    assert derive.work_auth_regions(resume) is None


def test_polarity_is_decided_per_clause_not_per_document() -> None:
    """The two_column.pdf shape: one sentence authorises, the next excludes. Reading the
    document as a whole would either authorise the US — a role that candidate cannot take
    — or drop the EU, hiding every job they can."""
    resume = ParsedResume(
        work_authorization="EU citizen. Requires H-1B sponsorship for roles in the United States."
    )

    assert derive.work_auth_regions(resume) == ["EU"]


def test_a_region_named_only_in_a_sponsorship_clause_is_excluded() -> None:
    resume = ParsedResume(work_authorization="Canadian citizen. Needs sponsorship for Germany.")

    assert derive.work_auth_regions(resume) == ["CA"]


@pytest.mark.parametrize(
    "stated",
    ["Current status is unclear", "Because of relocation", "Based in Ukraine", "Euro-based rates"],
)
def test_a_short_token_inside_a_longer_word_is_not_a_region(stated: str) -> None:
    """`us` is in "status" and "because", `uk` in "Ukraine", `eu` in "Euro". A substring
    match would authorise a country the résumé never named, which is the failure
    direction that costs a user real applications."""
    assert derive.work_auth_regions(ParsedResume(work_authorization=stated)) is None
