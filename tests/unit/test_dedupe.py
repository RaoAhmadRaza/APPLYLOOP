"""The dedupe key, as pure string rules.

Half of these assert that things *do not* merge. That is the point: a surviving
duplicate costs one wasted match, a false merge costs an application the user never got
to make, so every rule here is written to fail towards keeping two rows.
"""

import pytest
from schemas.enums import AtsType
from workers.scraping import dedupe
from workers.scraping.dedupe import dedupe_key, normalize_company, normalize_location

# ------------------------------------------------------------------- the merges we want


def test_the_gate_case_merges() -> None:
    """The M2 gate at the key level: a Greenhouse row and its LinkedIn twin. Legal
    suffix, abbreviated seniority, and a longer location string."""
    ats = dedupe_key("Acme Inc", "Senior Software Engineer", "San Francisco, CA")
    aggregator = dedupe_key(
        "Acme", "Sr. Software Engineer", "San Francisco, California, United States"
    )

    assert ats == aggregator == "acme|seniorsoftwareengineer|sanfrancisco"


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("Acme Inc", "acme"),
        ("Acme, LLC", "acme"),
        ("Acme GmbH", "acme"),
        ("Acme Pty Ltd", "acmepty"),
        ("Société Générale S.A.", "societegenerale"),
        # Never strip the only token — "Limited" alone is a company name.
        ("Limited", "limited"),
        ("  Creative Force  ", "creativeforce"),
    ],
)
def test_legal_suffixes_are_noise(company: str, expected: str) -> None:
    assert normalize_company(company) == expected


def test_an_eu_gender_tag_is_noise() -> None:
    assert dedupe_key("Zalando SE", "Backend Engineer (m/w/d)", "Berlin, Germany") == dedupe_key(
        "Zalando", "Backend Engineer", "Berlin"
    )


def test_a_trailing_requisition_id_is_noise() -> None:
    assert dedupe_key("Visa", "Sr. Manager - 744123", "Austin, TX") == dedupe_key(
        "Visa", "Senior Manager", "Austin"
    )


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("San Francisco, CA", "sanfrancisco"),
        ("San Francisco, California, United States", "sanfrancisco"),
        ("San Francisco", "sanfrancisco"),
        (None, ""),
        ("", ""),
    ],
)
def test_only_the_first_location_component_counts(location: str | None, expected: str) -> None:
    """One rule instead of a fifty-entry state table."""
    assert normalize_location(location) == expected


# --------------------------------------------------------------- the merges we refuse


def test_seniority_is_never_stripped() -> None:
    """Senior X and X are different jobs. The sr -> senior expansion runs the other
    way: it only merges titles that already mean the same thing."""
    assert dedupe_key("Acme", "Senior Engineer", "Austin") != dedupe_key(
        "Acme", "Engineer", "Austin"
    )


def test_multi_city_openings_of_one_title_stay_distinct() -> None:
    keys = {
        dedupe_key("Acme", "Software Engineer", city)
        for city in ("San Francisco, CA", "New York, NY", "London, UK")
    }

    assert len(keys) == 3


def test_a_hash_number_is_not_a_requisition_id() -> None:
    """Found in live Gopuff data: "Operations Associate, Bridgeport, #259" is a *store
    number*, not a requisition id, and stripping it merged two postings at two different
    facilities. A dash or a bracket is a requisition id; a hash is not reliably one."""
    assert dedupe_key("Gopuff", "Operations Associate, Bridgeport, #259", "Bridgeport, CT") != (
        dedupe_key("Gopuff", "Operations Associate, Bridgeport, #539", "Bridgeport, PA")
    )


def test_a_dash_delimited_requisition_id_is_still_noise() -> None:
    """The narrowing above must not cost the case the rule exists for."""
    assert dedupe_key("Visa", "Senior Manager - 744123", "Austin") == dedupe_key(
        "Visa", "Senior Manager", "Austin"
    )


def test_a_bare_trailing_year_is_not_a_requisition_id() -> None:
    """Stripping it would merge two different intake years. The id pattern requires a
    delimiter precisely so this stays true."""
    assert dedupe_key("Acme", "Summer Internship 2026", "Austin") != dedupe_key(
        "Acme", "Summer Internship 2025", "Austin"
    )


@pytest.mark.parametrize(
    ("company", "title"),
    [("", "Engineer"), ("   ", "Engineer"), ("Acme", ""), ("!!!", "Engineer")],
)
def test_an_unkeyable_row_gets_no_key(company: str, title: str) -> None:
    """NULL means "never merge this with anything", which is the safe reading."""
    assert dedupe_key(company, title, "Remote") is None


def test_the_separator_cannot_appear_inside_a_component() -> None:
    """Otherwise two different triples could render to one key."""
    key = dedupe_key("A|B", "C|D", "E|F")

    assert key is not None
    assert key.count("|") == 2


# --------------------------------------------------------------------- source priority


def test_every_ats_adapter_outranks_everything_else() -> None:
    """A typo here would silently let an aggregator outrank a first-party row and cost
    the survivor its apply URL — the exact thing the M2 gate asserts. Same reasoning as
    test_enums_match_checks.py: cross-check the table against the real registry."""
    from workers.scraping import ADAPTERS

    assert {dedupe.SOURCE_PRIORITY.get(module.SOURCE) for module in ADAPTERS.values()} == {0}


def test_every_feed_ranks_below_the_ats_layer_and_above_aggregators() -> None:
    from workers.scraping.feeds import FEEDS

    assert {dedupe.SOURCE_PRIORITY.get(source) for source in FEEDS} == {1}
    assert 0 < 1 < dedupe.AGGREGATOR_PRIORITY < dedupe.UNKNOWN_PRIORITY


def test_ats_other_is_not_a_winning_source() -> None:
    """`other` is what the registry writes for "we could not find a board". There is no
    adapter behind it, so a row claiming it must not outrank a real one."""
    assert AtsType.OTHER.value not in dedupe.SOURCE_PRIORITY
