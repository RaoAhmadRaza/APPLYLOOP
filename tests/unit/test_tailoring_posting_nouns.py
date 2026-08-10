"""A bullet may name the employer it is written to. It may not borrow their stack.

The letter path has always allowed the posting's company and title, on the argument that
naming the employer being applied to is not a claim about the candidate. The bullet path
did not, and the asymmetry cost honest rephrasings. Widening it to the same two strings is
bounded; widening it to "the posting's vocabulary" is class F2 in BAR.md §4, which is what
these tests pin apart.
"""

from schemas.tailoring import TailoredBullet
from workers.tailoring import validate


def _vault(bullet: str) -> validate.Vault:
    return validate.vault_from(
        {
            "claims": [
                {
                    "id": "B1",
                    "kind": "bullet",
                    "text": bullet,
                    "source": "work[0].highlights[0]",
                }
            ],
            "companies": ["Halcyon Freight"],
            "titles": ["Staff Software Engineer"],
        }
    )


def test_a_bullet_may_name_the_posting_company_and_title() -> None:
    vault = _vault("Cut checkout latency from 940ms to 205ms")
    bullet = TailoredBullet(
        text="Cut checkout latency from 940ms to 205ms, the Platform Engineer work at Stripe",
        evidence_id="B1",
    )

    report = validate.resume(
        bullets=[bullet], skills=[], vault=vault, company="Stripe", title="Platform Engineer"
    )

    assert report.kept == [bullet]


def test_the_posting_is_still_not_a_source_of_technologies() -> None:
    """Class F2: the vault says AWS, the posting says Azure, the bullet says Azure. The
    two proper nouns are admitted by name and nothing else is."""
    vault = _vault("Ran the deployment pipeline on AWS")
    bullet = TailoredBullet(text="Ran the deployment pipeline on Azure", evidence_id="B1")

    report = validate.resume(
        bullets=[bullet], skills=[], vault=vault, company="Stripe", title="Platform Engineer"
    )

    assert report.kept == []
    assert "Azure" in report.stripped[0].reason


def test_the_posting_nouns_default_to_absent() -> None:
    """The offline cases judge a bullet against a vault and no posting. Defaulting to
    empty is what keeps every one of them measuring what it measured before."""
    vault = _vault("Cut checkout latency from 940ms to 205ms")
    bullet = TailoredBullet(text="Cut checkout latency at Stripe", evidence_id="B1")

    report = validate.resume(bullets=[bullet], skills=[], vault=vault)

    assert report.kept == []
