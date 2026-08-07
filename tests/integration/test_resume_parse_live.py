"""M3's gate, against a real model.

    APPLYLOOP_LIVE_LLM=1 uv run pytest tests/integration/test_resume_parse_live.py

CLAUDE.md §9's M3 row, executable: "Three sample résumés parse with correct
skills/seniority/location/work-auth. Prefs store and retrieve. Evidence vault populated
for one test user." Four résumés are used rather than three, because the fourth costs
almost nothing and covers the DOCX path.

Deliberately not in CI, for two reasons rather than the usual one. A build must not go
red because a provider had a bad afternoon — and unlike the ATS and feed suites, this
one spends real money on every run.

Expectations live in `tests/fixtures/resumes/labels.json`, not inline here. That file is
build-sequence.md's "small labeled set", and keeping it next to the résumés means adding
a fixture is one edit rather than two.

Tolerances are deliberate. Skills are checked as a superset — a model naming *more* real
skills than the label set is doing its job. Seniority, location and work-auth are exact,
because those are the values M4 puts in a `WHERE` clause and "close enough" there means
dropping the wrong jobs.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from db.models import Evidence, Profile, User
from sqlalchemy import select
from sqlalchemy.orm import Session
from workers import llm
from workers.profiles import extract, parse, vault

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("APPLYLOOP_LIVE_LLM"),
        reason="calls a real model and spends real credit; set APPLYLOOP_LIVE_LLM=1",
    ),
    pytest.mark.skipif(
        not os.getenv("LLM_API_KEY"),
        reason="the parse stage no-ops without an API key, by design",
    ),
]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "resumes"
LABELS: dict[str, Any] = json.loads((FIXTURES / "labels.json").read_text())
RESUMES = sorted(name for name in LABELS if not name.startswith("_"))


@pytest.fixture(autouse=True)
def _fresh_settings() -> None:
    """`get_settings` is lru_cached, and the key arrives from the environment."""
    llm.get_settings.cache_clear()


def _parsed(session: Session, name: str) -> tuple[Profile, Any]:
    """Run the real stage over one fixture. Seeds the row, parses, returns both."""
    user = User(email=f"{name.replace('.', '-')}@example.test", auth_id=f"auth|{name}")
    session.add(user)
    session.flush()

    text = extract.to_markdown((FIXTURES / name).read_bytes(), Path(name).suffix)
    profile = Profile(user_id=user.id, master_resume=text)
    session.add(profile)
    session.flush()

    result = parse.parse_profile(session, profile)
    return profile, result


@pytest.mark.parametrize("name", RESUMES)
def test_the_promoted_columns_match_the_labels(session: Session, name: str) -> None:
    """The gate clause, field by field. Exact, not approximate: these four go into M4's
    `WHERE` clause, where being close means dropping the wrong jobs."""
    expected = LABELS[name]
    profile, _ = _parsed(session, name)

    assert profile.seniority == expected["seniority"], f"{name} seniority"
    assert profile.work_auth == expected["work_auth"], f"{name} work_auth"
    assert any(expected["location_contains"] in place for place in profile.locations), (
        f"{name} locations: {profile.locations}"
    )


@pytest.mark.parametrize("name", RESUMES)
def test_the_labelled_skills_are_all_extracted(session: Session, name: str) -> None:
    """A superset check. A model naming more real skills than the label set is doing its
    job; a model missing one is the regression."""
    profile, _ = _parsed(session, name)
    extracted = {
        skill.lower()
        for entry in profile.parsed_json["skills"]
        for skill in [entry.get("name") or "", *entry.get("keywords", [])]
        if skill
    }

    missing = [s for s in LABELS[name]["skills_include"] if s.lower() not in extracted]
    assert not missing, f"{name} missing {missing} from {sorted(extracted)}"


@pytest.mark.parametrize("name", RESUMES)
def test_years_of_experience_lands_in_the_labelled_range(session: Session, name: str) -> None:
    """A range rather than a value: the model reports dates and Python does the
    arithmetic, so the only thing at risk here is whether the *dates* were read right."""
    low, high = LABELS[name]["years_experience_between"]
    profile, _ = _parsed(session, name)

    years = profile.parsed_json["years_experience"]
    assert years is not None, f"{name} produced no dated roles"
    assert low <= years <= high, f"{name} years={years}, expected {low}-{high}"


@pytest.mark.parametrize("name", RESUMES)
def test_every_role_in_the_resume_is_found(session: Session, name: str) -> None:
    profile, result = _parsed(session, name)

    assert result.roles == LABELS[name]["roles"]


@pytest.mark.parametrize("name", RESUMES)
def test_the_vault_is_populated_and_nothing_in_it_was_invented(session: Session, name: str) -> None:
    """The gate's third clause and §3.3's guarantee, against a real model rather than a
    stub. Every stored claim must be findable in the résumé it came from — this is the
    assertion that would catch a model quietly paraphrasing."""
    profile, result = _parsed(session, name)

    stored = list(session.scalars(select(Evidence).where(Evidence.profile_id == profile.id)))
    assert stored, f"{name} produced an empty vault"
    assert profile.master_resume is not None
    for claim in stored:
        assert vault.is_supported(claim.text, profile.master_resume), (
            f"{name}: {claim.kind} claim not in the source: {claim.text!r}"
        )
    assert result.claims_stored == len(stored)


@pytest.mark.parametrize("name", RESUMES)
def test_the_model_copies_rather_than_paraphrases(session: Session, name: str) -> None:
    """The prompt's first rule, measured. A rejected claim is a *correct* fact the vault
    had to drop because the model rewrote it — real evidence lost, silently, unless this
    is watched. A small number is tolerable; a large one means the prompt regressed."""
    _, result = _parsed(session, name)

    total = result.claims_stored + result.claims_rejected
    assert total, f"{name} produced no claims at all"
    assert result.claims_rejected / total < 0.15, (
        f"{name} rejected {result.claims_rejected}/{total} claims — the model is paraphrasing"
    )
