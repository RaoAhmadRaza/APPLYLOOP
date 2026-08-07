"""Title banding and band comparison, moved out of `profiles/derive.py` for M4.

The first test is the important one. `RANK` exists because two orderings already in the
codebase look like rank and neither is; a comparison written against either would be
wrong in a way no other test notices.
"""

import pytest
from schemas.enums import Seniority
from workers import seniority

# ---- the trap RANK exists to close ----------------------------------------------


def test_neither_existing_ordering_is_a_rank() -> None:
    """Pinned, because both are the obvious thing to reach for and both are wrong."""
    members = list(Seniority)

    # Declaration order: intern, junior, mid, senior, staff, principal, lead, director.
    # It says a lead outranks a principal, and that staff outranks senior *and* lead.
    assert members.index(Seniority.LEAD) > members.index(Seniority.PRINCIPAL)

    # Match-precedence order in _TITLE_BANDS: director, principal, staff, lead, senior,
    # intern, junior. It says an intern outranks a junior.
    match_order = [level for level, _ in seniority.TITLE_BANDS]
    assert match_order.index(Seniority.INTERN) < match_order.index(Seniority.JUNIOR)

    # RANK disagrees with both, on purpose.
    assert seniority.RANK[Seniority.LEAD] < seniority.RANK[Seniority.PRINCIPAL]
    assert seniority.RANK[Seniority.INTERN] < seniority.RANK[Seniority.JUNIOR]


def test_lead_and_staff_are_peers_not_a_ladder() -> None:
    """Different tracks. Guessing a direction would drop real matches on one of them."""
    assert seniority.RANK[Seniority.LEAD] == seniority.RANK[Seniority.STAFF]
    assert seniority.distance(Seniority.LEAD, Seniority.STAFF) == 0


def test_every_seniority_member_has_a_rank() -> None:
    """A member added to the enum without a rank would KeyError inside a filter."""
    assert set(seniority.RANK) == set(Seniority)


# ---- band() ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Director of Engineering", Seniority.DIRECTOR),
        ("VP, Platform", Seniority.DIRECTOR),
        ("Principal Engineer", Seniority.PRINCIPAL),
        ("Staff Software Engineer", Seniority.STAFF),
        ("Solutions Architect", Seniority.STAFF),
        ("Engineering Manager", Seniority.LEAD),
        ("Tech Lead", Seniority.LEAD),
        ("Senior Backend Engineer", Seniority.SENIOR),
        ("Sr. Data Engineer", Seniority.SENIOR),
        ("Software Engineering Intern", Seniority.INTERN),
        ("Junior Developer", Seniority.JUNIOR),
        ("Operations Associate", Seniority.JUNIOR),
    ],
)
def test_band_reads_the_stated_level(title: str, expected: Seniority) -> None:
    assert seniority.band(title) == expected


def test_the_most_specific_keyword_wins() -> None:
    """ "Senior Staff Engineer" is Staff. Match precedence, not string position."""
    assert seniority.band("Senior Staff Engineer") == Seniority.STAFF


def test_a_word_boundary_is_required() -> None:
    """Or "principal" fires on "principally" and "sr" on "customers"."""
    assert seniority.band("Principally Remote Engineer") is None
    assert seniority.band("Customer Success Engineer") is None


@pytest.mark.parametrize("title", ["Software Engineer", "Backend Developer", "", None])
def test_a_title_that_states_no_band_is_unknown_not_junior(title: str | None) -> None:
    """79% of real postings in the pool carry no seniority keyword at all.

    Unknown must never be read as a failed filter — the same polarity as
    `jobs.remote_mode IS NULL`. Returning JUNIOR here would drop four postings in five.
    """
    assert seniority.band(title) is None


# ---- distance() ------------------------------------------------------------------


def test_distance_is_signed_from_the_first_argument() -> None:
    """M4's label needs the direction: a job one band up is a REACH, one band down is not."""
    assert seniority.distance(Seniority.MID, Seniority.SENIOR) == 1
    assert seniority.distance(Seniority.SENIOR, Seniority.MID) == -1


@pytest.mark.parametrize(
    ("a", "b"),
    [(None, Seniority.SENIOR), (Seniority.SENIOR, None), (None, None)],
)
def test_distance_is_unknown_when_either_side_is(a: Seniority | None, b: Seniority | None) -> None:
    """None, never 0. A caller comparing `distance > 1` must not read unknown as a match."""
    assert seniority.distance(a, b) is None
