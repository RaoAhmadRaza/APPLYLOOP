"""The alias table changes spelling and never possession — in one direction.

The direction is the whole safety argument, and case `S-06` in the fabrication set is the
paid half of it. These are the free half: they fail in milliseconds if someone makes the
table symmetric, which is the change that looks obviously correct and is not.
"""

from schemas.tailoring import TailoredBullet
from workers.tailoring import aliases, validate


def _vault(*skills: str, bullets: tuple[str, ...] = ()) -> validate.Vault:
    claims = [
        {"id": f"E{index + 1}", "kind": "skill", "text": text, "source": f"skills[{index}]"}
        for index, text in enumerate(skills)
    ]
    claims += [
        {
            "id": f"B{index + 1}",
            "kind": "bullet",
            "text": text,
            "source": f"work[0].highlights[{index}]",
        }
        for index, text in enumerate(bullets)
    ]
    return validate.vault_from({"claims": claims, "companies": ["Acme"], "titles": ["Engineer"]})


def test_a_vault_spelling_licenses_its_fuller_form() -> None:
    """The case this exists for: the résumé says Postgres, the posting and the ATS say
    PostgreSQL, and the candidate loses a keyword on a skill they genuinely have."""
    report = validate.resume(bullets=[], skills=["PostgreSQL"], vault=_vault("Postgres"))

    assert report.skills == ["PostgreSQL"]
    assert report.fabricated_skills == []


def test_a_vault_spelling_does_not_license_its_shorter_form() -> None:
    """Case S-06, offline. A symmetric table would make that committed case pass, and
    blocking is right on the merits: the résumé says PostgreSQL, so the résumé should."""
    report = validate.resume(bullets=[], skills=["Postgres"], vault=_vault("PostgreSQL"))

    assert report.skills == []
    assert report.fabricated_skills == ["Postgres"]


def test_an_alias_is_unreachable_without_the_skill() -> None:
    """An alias is only ever reached *from* a stored claim, so it cannot add a skill —
    the one thing §3.3 forbids."""
    report = validate.resume(bullets=[], skills=["Kubernetes"], vault=_vault("Docker"))

    assert report.fabricated_skills == ["Kubernetes"]


def test_a_group_line_expands_through_its_members() -> None:
    """Splitting runs first, so `K8s` inside a group line expands and the line as a whole
    does not — which is also why expansion is whole-token and never substring."""
    report = validate.resume(
        bullets=[], skills=["Kubernetes"], vault=_vault("Infrastructure: K8s, Terraform")
    )

    assert report.skills == ["Kubernetes"]


def test_a_bullet_may_use_the_fuller_spelling_of_a_word_its_claim_names() -> None:
    vault = _vault(bullets=("Migrated 40 services onto Postgres",))
    bullet = TailoredBullet(text="Migrated 40 services onto PostgreSQL", evidence_id="B1")

    report = validate.resume(bullets=[bullet], skills=[], vault=vault)

    assert report.kept == [bullet]


def test_a_bullet_may_not_name_a_technology_its_claim_never_did() -> None:
    """The rule that stops this being a synonym engine. AWS and Azure are not aliases and
    must never become entries — that is class F2 wearing a lookup table."""
    vault = _vault(bullets=("Migrated 40 services onto AWS",))
    bullet = TailoredBullet(text="Migrated 40 services onto Azure", evidence_id="B1")

    report = validate.resume(bullets=[bullet], skills=[], vault=vault)

    assert report.kept == []
    assert "Azure" in report.stripped[0].reason


def test_expansion_is_whole_string_not_substring() -> None:
    """`js` inside `jsonschema` must not license `JavaScript`."""
    assert aliases.expand("JS") == "JavaScript"
    assert aliases.expand("jsonschema") is None
