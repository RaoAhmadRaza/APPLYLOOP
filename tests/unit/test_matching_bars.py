"""The disqualifiers a pure function can find, and the ones it must not invent.

Every case here was a real pair in the golden set. The reason this file exists at all is
measured: asking the model for these categories dropped its extraction of the ones that
genuinely need reading from 8/8 to 9/13, because a prompt is not a list and attention is
finite. Moving them here made them free, deterministic, and checkable without spending a
model call — and the failure direction that matters, a bar invented against a job the
candidate could have had, is asserted below rather than hoped for.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from db.models import Job
from schemas.profile import ProfileRead
from workers.matching import bars


def _job(**kwargs: Any) -> Job:
    return Job(
        id=uuid.uuid4(),
        source="lever",
        external_id=f"x:{uuid.uuid4()}",
        title=kwargs.pop("title", "Backend Engineer"),
        company=kwargs.pop("company", "Acme"),
        locations=kwargs.pop("locations", []),
        description=kwargs.pop("description", ""),
        url="https://example.com",
        raw_json={},
        **kwargs,
    )


def _profile(**kwargs: Any) -> ProfileRead:
    now = datetime.now(UTC)
    return ProfileRead.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "created_at": now,
            "updated_at": now,
            "parsed_json": {},
            "prefs_json": {},
            "locations": [],
            **kwargs,
        }
    )


# ---- country scope: the case no quoted span can catch ----------------------------


def test_a_role_scoped_outside_the_profiles_authorisation_is_barred() -> None:
    """ "Remote — United States" against a UK candidate needing sponsorship.

    The posting states a location, not a refusal, so there is nothing for a model to
    quote — and it is still a job the candidate cannot take.
    """
    job = _job(title="Revenue Management Analyst", locations=["United States"])
    profile = _profile(work_auth_regions=["GB"])

    found = bars.check(job, profile, "")

    assert found and "United States" not in found[0]  # the reason names regions, not prose
    assert "US" in found[0] and "GB" in found[0]


def test_a_role_inside_the_authorisation_is_not_barred() -> None:
    job = _job(locations=["United Kingdom"])

    assert bars.check(job, _profile(work_auth_regions=["GB"]), "") == []


def test_eu_authorisation_reaches_a_member_state() -> None:
    """An EU citizen can take a role in Poland. Treating the bloc as opaque would bar
    every European job for every European candidate."""
    job = _job(locations=["Kraków, Poland"])

    assert bars.check(job, _profile(work_auth_regions=["EU"]), "") == []


@pytest.mark.parametrize(
    "locations",
    [[], ["Anywhere in the World"], ["Remote"], ["Atlantis"], ["Worldwide"]],
)
def test_silence_and_globality_never_bar(locations: list[str]) -> None:
    """filters.py's polarity, one rung lower. A place we cannot map is not a place the
    candidate is excluded from — and "Anywhere in the World" is the opposite of one."""
    assert bars.check(_job(locations=locations), _profile(work_auth_regions=["GB"]), "") == []


def test_an_unstated_authorisation_never_bars() -> None:
    """`None` means the résumé did not say. Barring on that would hide most of the pool
    from every user whose résumé omits a sentence most résumés omit."""
    job = _job(locations=["United States"])

    assert bars.check(job, _profile(work_auth_regions=None), "") == []


def test_authorised_nowhere_bars_a_located_role() -> None:
    """`[]` is different from `None`: the résumé stated authorisation and it is nowhere."""
    job = _job(locations=["United States"])

    assert bars.check(job, _profile(work_auth_regions=[]), "") != []


# ---- language: an explicit demand, not the language of the posting ---------------


def test_a_required_language_the_resume_never_mentions_is_barred() -> None:
    job = _job(description="Bilingual English/Mandarin is required to coordinate overseas.")

    found = bars.check(job, _profile(), "Python, Go, SQL")

    assert found and "Mandarin" in found[0]


def test_a_required_language_the_resume_does_mention_is_not_barred() -> None:
    job = _job(description="Fluent in German and English.")

    assert bars.check(job, _profile(), "Languages: German, English") == []


def test_a_posting_that_merely_mentions_a_language_is_not_a_bar() -> None:
    """Only an explicit demand fires. A job that lists German as a nice-to-have, or that
    happens to name a language in passing, excludes nobody."""
    job = _job(description="German language skills are a plus. Our Berlin office is lovely.")

    assert bars.check(job, _profile(), "Python") == []


# ---- eligibility: an intake open only to a career stage --------------------------


def test_a_student_only_programme_bars_a_senior_profile() -> None:
    job = _job(
        title="Binance Accelerator Program - Data Scientist",
        description="Who may apply: Current university students and recent graduates.",
    )

    found = bars.check(job, _profile(seniority="senior"), "")

    assert found and "students" in found[0]


def test_the_same_programme_does_not_bar_a_junior() -> None:
    """The bar is a property of the pair. A junior is who the programme is for, and a
    rule that fired on the posting alone would hide it from exactly the right person."""
    job = _job(description="Who may apply: Current university students and recent graduates.")

    assert bars.check(job, _profile(seniority="junior"), "") == []


def test_an_ordinary_posting_trips_nothing() -> None:
    """The common case, and the one that decides recall. Most jobs bar nobody."""
    job = _job(
        locations=["Berlin, Germany"],
        description="We are looking for a backend engineer. Kubernetes is a plus.",
    )
    profile = _profile(work_auth_regions=["EU"], seniority="senior")

    assert bars.check(job, profile, "Python, Go") == []
