"""M3's stage against a real Postgres: seed a profile, run it, assert the row delta.

The model is the only thing faked, and only because a test cannot assert on a live one's
output. Everything else — the session, the constraints, the vault, the event — is real,
per Part 10's "no mocking of other stages".

The parse is run twice in several tests on purpose. A re-upload is the normal case, and
the two things that must survive it are the user's own edits and the user's own vault
claims.
"""

from typing import Any

import pytest
from db.models import Event, Evidence, Profile, User
from schemas.enums import EvidenceKind, EvidenceOrigin, Seniority, WorkAuth
from schemas.resume import ParsedResume
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers import llm
from workers.profiles import parse

RESUME = """\
# Ada Lovelace
Berlin, DE

## Experience

### Staff Engineer, Acme GmbH (2021-03 - present)
- Cut p95 checkout latency from 900ms to 210ms by adding a read-through cache
- Led the migration of 40 services from Nomad to Kubernetes

### Backend Engineer, Beta Ltd (2016-01 - 2021-02)
- Built the billing reconciliation pipeline processing 2M events a day

## Education
Technische Universitat Berlin - BSc Computer Science

## Skills
Languages: Python, Go, SQL

Authorized to work in the EU without sponsorship.
"""

EXTRACTED: dict[str, Any] = {
    "basics": {"name": "Ada Lovelace", "location": {"city": "Berlin", "region": "DE"}},
    "work": [
        {
            "name": "Acme GmbH",
            "position": "Staff Engineer",
            "start_date": "2021-03",
            "end_date": None,
            "highlights": [
                "Cut p95 checkout latency from 900ms to 210ms by adding a read-through cache",
                "Led the migration of 40 services from Nomad to Kubernetes",
            ],
        },
        {
            "name": "Beta Ltd",
            "position": "Backend Engineer",
            "start_date": "2016-01",
            "end_date": "2021-02",
            "highlights": ["Built the billing reconciliation pipeline processing 2M events a day"],
        },
    ],
    "education": [{"institution": "Technische Universitat Berlin"}],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go", "SQL"]}],
    "work_authorization": "Authorized to work in the EU without sponsorship",
}


@pytest.fixture
def profile(session: Session) -> Profile:
    user = User(email="ada@example.test", auth_id="auth|ada")
    session.add(user)
    session.flush()
    row = Profile(user_id=user.id, master_resume=RESUME)
    session.add(row)
    session.flush()
    return row


def _model(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any] | None = None) -> None:
    """Answer with a fixed extraction. The model's accuracy is the live suite's job."""

    def fake(schema: type[ParsedResume], *, system: str, user: str) -> ParsedResume:
        return ParsedResume.model_validate(payload if payload is not None else EXTRACTED)

    monkeypatch.setattr(llm, "complete_json", fake)


def _claims(session: Session, profile: Profile) -> set[tuple[str, str]]:
    return {
        (row.kind, row.text)
        for row in session.scalars(select(Evidence).where(Evidence.profile_id == profile.id))
    }


def _latest_event(session: Session, event_type: str) -> Event:
    return session.scalars(
        select(Event).where(Event.type == event_type).order_by(Event.id.desc()).limit(1)
    ).one()


# ------------------------------------------------------------------------- the parse


def test_the_parse_writes_the_structured_record(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch)

    result = parse.parse_profile(session, profile)

    assert result.roles == 2
    assert profile.parsed_json["basics"]["name"] == "Ada Lovelace"
    assert [role["position"] for role in profile.parsed_json["work"]] == [
        "Staff Engineer",
        "Backend Engineer",
    ]


def test_years_of_experience_lands_on_the_record_not_in_the_model_call(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub never returns one. It is on the row because Python computed it — which
    is the whole reason M4 can trust a number it filters on."""
    _model(monkeypatch)

    parse.parse_profile(session, profile)

    assert profile.parsed_json["years_experience"] == pytest.approx(10.4, abs=0.2)


def test_the_promoted_columns_are_filled_from_the_parse(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3's gate: skills, seniority, location and work-auth, correct."""
    _model(monkeypatch)

    result = parse.parse_profile(session, profile)

    assert profile.seniority == Seniority.STAFF.value
    assert profile.work_auth == WorkAuth.VISA_HOLDER.value
    # Where that authorisation applies. `work_auth` alone is country-less, which is how a
    # UK citizen came to score 94 on an ITAR-restricted role in M4's first gate run.
    assert profile.work_auth_regions == ["EU"]
    assert profile.locations == ["Berlin, DE"]
    assert sorted(result.promoted) == [
        "locations",
        "seniority",
        "work_auth",
        "work_auth_regions",
    ]


def test_the_vault_is_populated_and_every_claim_is_in_the_resume(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate clause "evidence vault populated for one test user", plus the invariant
    that makes it worth anything."""
    _model(monkeypatch)

    parse.parse_profile(session, profile)

    stored = _claims(session, profile)
    assert (EvidenceKind.SKILL.value, "Python") in stored
    assert (EvidenceKind.TITLE.value, "Staff Engineer") in stored
    assert (
        EvidenceKind.BULLET.value,
        "Led the migration of 40 services from Nomad to Kubernetes",
    ) in stored
    for _, text in stored:
        assert text.lower().replace(" ", "") in RESUME.lower().replace(" ", "")


def test_a_claim_the_resume_does_not_support_never_reaches_the_vault(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.3 at the stage level. A model that invented "Rust" would otherwise have M5
    declare a fabricated bullet traceable — the validator working perfectly and proving
    nothing."""
    fabricated = {
        **EXTRACTED,
        "skills": [{"name": "Languages", "keywords": ["Python", "Rust"]}],
    }
    _model(monkeypatch, fabricated)

    result = parse.parse_profile(session, profile)

    assert (EvidenceKind.SKILL.value, "Rust") not in _claims(session, profile)
    assert (EvidenceKind.SKILL.value, "Python") in _claims(session, profile)
    assert result.claims_rejected == 1


def test_the_run_is_recorded_with_its_counts(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.2 wants per-run counts and §3.7 wants alerting on volume. `claims_rejected`
    is the one that matters: non-zero means the prompt regressed and real evidence is
    being dropped silently unless somebody is counting."""
    _model(monkeypatch)

    parse.parse_profile(session, profile)

    event = _latest_event(session, "profile.parsed")
    assert event.user_id == profile.user_id
    assert event.payload_json["roles"] == 2
    assert event.payload_json["claims_rejected"] == 0
    assert event.payload_json["claims_stored"] > 0


# ------------------------------------------------------------------------- re-parsing


def test_a_second_parse_writes_no_duplicate_claims(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent. The unique constraint would refuse a duplicate, so a rebuild that
    did not clear first would raise rather than converge."""
    _model(monkeypatch)
    parse.parse_profile(session, profile)
    first = _claims(session, profile)

    parse.parse_profile(session, profile)

    assert _claims(session, profile) == first


def test_a_re_parse_drops_claims_the_new_resume_no_longer_makes(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild, not merge. A skill removed from the résumé must leave the vault, or M5
    would keep grounding bullets on evidence the user deleted."""
    _model(monkeypatch)
    parse.parse_profile(session, profile)
    assert (EvidenceKind.SKILL.value, "Go") in _claims(session, profile)

    profile.master_resume = RESUME.replace("Python, Go, SQL", "Python, SQL")
    _model(monkeypatch, {**EXTRACTED, "skills": [{"name": "Languages", "keywords": ["Python"]}]})
    parse.parse_profile(session, profile)

    assert (EvidenceKind.SKILL.value, "Go") not in _claims(session, profile)


def test_an_empty_parse_is_refused_and_the_previous_vault_survives(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A re-parse is destructive, so an empty answer must not be believed.**

    Observed live on 2026-08-10: `deepseek-v4-flash` returned no roles and no skills for
    1,255 characters of résumé that had parsed to 20 claims a minute earlier. Nothing
    raised, nothing errored, and the vault was gone — `rebuild` had already deleted every
    parsed claim before inserting the nothing it was given.
    """
    _model(monkeypatch)
    parse.parse_profile(session, profile)
    before = _claims(session, profile)
    assert before

    _model(monkeypatch, {"basics": {"name": "Dana Whitfield"}, "work": [], "skills": []})
    with pytest.raises(parse.ParseEmptyError):
        parse.parse_profile(session, profile)

    assert _claims(session, profile) == before


def test_a_genuinely_empty_resume_still_parses_to_nothing(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard fires on volume, not on emptiness as such. A near-empty upload really
    does parse to nothing, and a loud failure landing on the honest case is worse than
    the defect it was written for."""
    profile.master_resume = "Dana Whitfield"
    profile.resume_url = None
    _model(monkeypatch, {"basics": {"name": "Dana Whitfield"}, "work": [], "skills": []})

    result = parse.parse_profile(session, profile)

    assert result.roles == 0
    assert result.claims_stored == 0


def test_a_re_parse_keeps_claims_the_user_added_by_hand(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason `origin` exists. Without it, uploading a new résumé silently deletes
    every project a person entered themselves."""
    session.add(
        Evidence(
            profile_id=profile.id,
            kind=EvidenceKind.BULLET.value,
            text="Maintained the internal design system for three years",
            origin=EvidenceOrigin.USER.value,
        )
    )
    session.flush()
    _model(monkeypatch)

    result = parse.parse_profile(session, profile)

    assert (
        EvidenceKind.BULLET.value,
        "Maintained the internal design system for three years",
    ) in _claims(session, profile)
    assert result.claims_stored > 0


def test_a_re_parse_does_not_overwrite_a_column_the_user_set(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person who corrected their own seniority has said something the résumé cannot
    contradict. The promotion rule fills only what is empty."""
    profile.seniority = Seniority.PRINCIPAL.value
    profile.locations = ["Remote", "Berlin"]
    session.flush()
    _model(monkeypatch)

    result = parse.parse_profile(session, profile)

    assert profile.seniority == Seniority.PRINCIPAL.value
    assert profile.locations == ["Remote", "Berlin"]
    assert "seniority" not in result.promoted
    assert "locations" not in result.promoted


# ------------------------------------------------------------------------ the edges


def test_a_resume_with_no_dates_still_parses(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sparse record is a valid outcome; refusing it is not. This is the shape that
    breaks the closest prior art, whose parser raises on a missing section."""
    profile.master_resume = "Ada Lovelace\nPython, Go"
    session.flush()
    _model(monkeypatch, {"skills": [{"name": None, "keywords": ["Python", "Go"]}]})

    result = parse.parse_profile(session, profile)

    assert result.roles == 0
    assert profile.parsed_json["years_experience"] is None
    assert profile.seniority is None
    assert (EvidenceKind.SKILL.value, "Python") in _claims(session, profile)


def test_deleting_the_profile_takes_the_vault(
    session: Session, profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch)
    parse.parse_profile(session, profile)
    assert _vault_size(session) > 0

    session.delete(profile)
    session.flush()

    assert _vault_size(session) == 0


def _vault_size(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Evidence)) or 0
