"""The ATS keyword readout: reported, deterministic, and bounded by the vault.

Its one job is to be a number nobody has to trust a model for. The tests that matter are
the ones pinning what it must NOT count — a posting keyword the candidate does not hold,
and a token that merely contains a skill's letters.
"""

from workers.tailoring import keywords

DESCRIPTION = (
    "We are hiring a backend engineer. You will work in Python and Go, on PostgreSQL, "
    "and deploy with Kubernetes. Experience with Rust is a plus. Ongoing work on Golang "
    "tooling is part of the role."
)


def test_only_skills_the_posting_names_are_counted() -> None:
    """The denominator is the overlap, not the vault and not the posting. Counting every
    vault skill would punish a résumé for omitting things this employer never asked for."""
    result = keywords.coverage(
        claims=["Languages: Python, Go, Haskell"],
        description=DESCRIPTION,
        rendered="Python Go Haskell",
    )

    # Haskell is in the vault and not in the posting, so it is in neither half.
    assert result["keywords_total"] == 2
    assert result["keywords_matched"] == 2


def test_a_posting_keyword_the_candidate_lacks_is_never_counted() -> None:
    """Rust is all over the posting and nowhere in the vault. A metric that counted it
    would read as "you are missing Rust", which is one short step from suggesting the
    document say otherwise."""
    result = keywords.coverage(
        claims=["Languages: Python"], description=DESCRIPTION, rendered="Python"
    )

    assert result["keywords_missing"] == []
    assert result["keywords_total"] == 1


def test_a_held_keyword_left_off_the_document_is_reported_missing() -> None:
    result = keywords.coverage(
        claims=["Languages: Python, Go"], description=DESCRIPTION, rendered="Python"
    )

    assert result["keywords_matched"] == 1
    assert result["keywords_missing"] == ["Go"]


def test_a_skill_is_matched_as_whole_tokens_not_as_a_substring() -> None:
    """`Go` is inside `Golang` and `Ongoing`, both of which this posting contains. A
    squashed-blob check counts them and inflates the number on nearly every posting."""
    result = keywords.coverage(claims=["Languages: Go"], description=DESCRIPTION, rendered="")

    # Present as a real token too, so this asserts the edges rather than absence.
    assert result["keywords_total"] == 1
    assert result["keywords_missing"] == ["Go"]

    absent = keywords.coverage(
        claims=["Languages: Go"],
        description="Ongoing Golang tooling, no standalone mention.",
        rendered="",
    )
    assert absent["keywords_total"] == 0


def test_the_fuller_vendor_spelling_counts_for_the_shorter_one() -> None:
    """The vault says Postgres, the posting says PostgreSQL. Same skill, and the whole
    reason the alias table exists."""
    result = keywords.coverage(
        claims=["Infrastructure: Postgres"], description=DESCRIPTION, rendered="PostgreSQL"
    )

    assert result["keywords_matched"] == 1
    assert result["keywords_missing"] == []


def test_a_group_heading_is_not_a_keyword() -> None:
    """`Languages` and `Infrastructure` are headings. Counting them would inflate both
    sides with words no ATS looks for."""
    assert keywords.vault_skills(["Languages: Python, Go"]) == ["Python", "Go"]
    # A claim with no delimiter is already one skill and keeps its whole text.
    assert keywords.vault_skills(["Python"]) == ["Python"]


def test_a_skill_named_only_in_a_bullet_still_counts() -> None:
    """An ATS reads the whole document. A skill surfaced in a bullet is as visible as one
    in the skills section."""
    result = keywords.coverage(
        claims=["Infrastructure: Kubernetes"],
        description=DESCRIPTION,
        rendered="Migrated 40 services onto Kubernetes",
    )

    assert result["keywords_matched"] == 1
