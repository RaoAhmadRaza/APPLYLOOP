"""The letter is checked for facts, not for vocabulary — and these pin the difference.

Loosening a fabrication rule earns a stricter test file than tightening one. Every case
here is either something the old rule rejected and should not have, or something the new
rule must still refuse. The résumé path is untouched and `test_fabrication_guard` governs
it; nothing in this file may be used to argue about a bullet.
"""

from schemas.tailoring import CoverLetterParagraph
from workers.tailoring import validate

CLAIM = "Cut checkout latency from 940ms to 205ms by adding a Redis cache"


def _vault() -> validate.Vault:
    return validate.vault_from(
        {
            "claims": [
                {"id": "E1", "kind": "bullet", "text": CLAIM, "source": "work[0].highlights[0]"}
            ],
            "companies": ["Halcyon Freight"],
            "titles": ["Staff Software Engineer"],
        }
    )


def _check(text: str) -> validate.LetterReport:
    return validate.cover_letter(
        paragraphs=[CoverLetterParagraph(text=text, evidence_ids=["E1"])],
        vault=_vault(),
        company="Stripe",
        title="Platform Engineer",
    )


# ---- what the old rule refused and should not have ---------------------------------


def test_lowercase_prose_is_allowed() -> None:
    """The exact words ten live pairs died on: backend, infrastructure, team, migrations,
    accountabilities, experience. None is a fabrication and no word list fixes it, because
    the next paragraph needs different ones."""
    report = _check(
        "My backend experience spans the infrastructure and migrations a team owns, and "
        "the accountabilities that come with it."
    )

    assert report.rejected == []
    assert not report.blocked


def test_a_paragraph_may_argue_from_a_claim_rather_than_restate_it() -> None:
    report = _check(
        "I cut checkout latency from 940ms to 205ms with a Redis cache, and I would bring "
        "that same instinct for measurement to Stripe."
    )

    assert report.rejected == []


def test_an_evidence_handle_written_into_the_prose_is_stripped_not_rejected() -> None:
    """**Our label, not the candidate's claim.** `select.py` mints E1..E20 and the model
    writes them into the sentence as well as into `evidence_ids`; `_DIGITS` then reads
    `E11` as the number 11 and rejects a paragraph that invented nothing.

    Six consecutive live letters died on "the number 11/12/13" against a vault whose
    letter claims were exactly E11, E12, E13, E15, E16, E18.
    """
    report = _check("I cut checkout latency with a Redis cache (E1), and would do it again.")

    assert report.rejected == []
    # Stripped rather than ignored: this text is what gets rendered onto the PDF.
    assert "E1" not in report.paragraphs[0].text
    assert "cache, and would" in report.paragraphs[0].text


def test_handles_are_stripped_in_every_shape_the_model_writes_them() -> None:
    assert validate.strip_handles("Work (E11) shipped.") == "Work shipped."
    assert validate.strip_handles("Work [E11, E12] shipped.") == "Work shipped."
    assert validate.strip_handles("As E11 shows, it shipped.") == "As shows, it shipped."
    # A real number is not a handle and must survive untouched.
    assert validate.strip_handles("Cut latency to 205ms.") == "Cut latency to 205ms."


# ---- what it must still refuse -----------------------------------------------------


def test_a_number_the_evidence_never_stated_is_still_refused() -> None:
    """The rule that earns its keep. Three of five live pairs failed here, every one a
    years-of-experience total the résumé never made, and `the number 13` and `the number
    17` were the two real catches on gpt-5. This does not move."""
    report = _check("Over 13 years I have cut latency and shipped platforms.")

    assert report.blocked
    assert "the number 13" in report.rejected[0].reason


def test_a_spelled_out_number_is_refused_exactly_like_a_digit() -> None:
    """**Found by the guard, not by design.** The first version of this rule checked
    digits only, and committed case CL-03 — "I have spent nine years building distributed
    systems at scale" — walked straight through it. A number is a number however it is
    spelled, and without this the model simply writes the word."""
    report = _check("I have spent nine years cutting latency on checkout paths.")

    assert report.blocked
    assert "nine" in report.rejected[0].reason


def test_a_number_word_the_evidence_does_state_is_allowed() -> None:
    """The counterpart, so the rule is a check rather than a ban."""
    vault = validate.vault_from(
        {
            "claims": [
                {
                    "id": "E1",
                    "kind": "bullet",
                    "text": "Led nine engineers through the checkout rebuild",
                    "source": "work[0].highlights[0]",
                }
            ],
            "companies": ["Halcyon Freight"],
            "titles": ["Staff Software Engineer"],
        }
    )

    report = validate.cover_letter(
        paragraphs=[
            CoverLetterParagraph(text="I led nine engineers through it.", evidence_ids=["E1"])
        ],
        vault=vault,
        company="Stripe",
        title="Platform Engineer",
    )

    assert report.rejected == []


def test_a_number_moved_from_another_role_is_still_refused() -> None:
    """F4's real shape: a true number in a place it was never true. 940 belongs to the
    cited claim; 4200 belongs to a different one, so the cited claim cannot support it."""
    report = _check("I have supported 4200 concurrent sessions on that path.")

    assert report.blocked


def test_a_technology_the_vault_never_named_is_still_refused() -> None:
    """Class F2, which is the whole reason capitalised tokens stay checked: the vault says
    Redis, the posting says Kafka, and the letter may not say Kafka."""
    report = _check("I cut checkout latency by putting Kafka in front of the hot path.")

    assert report.blocked
    assert "Kafka" in report.rejected[0].reason


def test_a_technology_is_refused_at_the_start_of_a_sentence_too() -> None:
    """Skipping sentence-initial tokens would leave exactly this hole, and a model that
    opens with the invented technology is making the same claim as one that does not."""
    report = _check("Kafka underpinned the checkout path I rebuilt.")

    assert report.blocked
    assert "Kafka" in report.rejected[0].reason


def test_an_employer_the_candidate_never_worked_for_is_still_refused() -> None:
    report = _check("At Netflix I cut checkout latency from 940ms to 205ms.")

    assert report.blocked
    assert "Netflix" in report.rejected[0].reason


def test_the_posting_company_and_title_remain_the_only_two_allowed() -> None:
    """Naming the employer you are writing to is not a claim about the candidate."""
    assert _check("I am applying for the Platform Engineer opening at Stripe.").rejected == []


def test_a_technology_the_vault_does_name_is_allowed() -> None:
    assert _check("Redis was the right tool for that cache.").rejected == []


def test_one_bad_paragraph_still_fails_the_whole_letter() -> None:
    """Unchanged, and deliberately: a paragraph removed from the middle of an argument
    leaves a hole, which is a different defect from an ungrounded sentence."""
    report = validate.cover_letter(
        paragraphs=[
            CoverLetterParagraph(text="Redis was the right tool.", evidence_ids=["E1"]),
            CoverLetterParagraph(text="I have 13 years of it.", evidence_ids=["E1"]),
        ],
        vault=_vault(),
        company="Stripe",
        title="Platform Engineer",
    )

    assert report.blocked
    assert len(report.rejected) == 1
