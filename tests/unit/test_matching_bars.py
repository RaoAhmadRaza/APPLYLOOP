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


def test_a_remote_posting_scoped_to_one_country_is_still_scoped() -> None:
    """**"Remote, United States" is the most common location string in the pool.**

    The globality check used to run first and match on the word "remote", which read
    every US remote posting as open to the world and disabled this bar entirely. Named
    regions win.
    """
    job = _job(locations=["Remote, United States"])

    assert bars.check(job, _profile(work_auth_regions=["GB"]), "") != []


def test_a_posting_listing_continents_excludes_nobody() -> None:
    """The opposite direction. "Americas, Europe, Asia, Africa, Oceania" names regions —
    including EU — but it is a breadth statement, not a scope, and barring a US candidate
    from it because it says Europe would be exactly backwards."""
    job = _job(locations=["Americas, Europe, Asia, Africa, Oceania"])

    assert bars.check(job, _profile(work_auth_regions=["US"]), "") == []


# ---- job family: engineering vocabulary in a job that is not engineering ---------


def test_a_sales_engineering_posting_is_barred_for_someone_who_has_never_sold() -> None:
    """**The false positive that blocked M4's gate at precision 0.75.**

    `Sales Engineer Enterprise`, remote in the candidate's own state, listing her exact
    stack, scored 53. A sales-engineering posting genuinely asks for Python and AWS, so
    coverage — a ratio over the requirements the posting states — rates it well and is
    right to. The job is still not hers.
    """
    job = _job(
        title="Sales Engineer Enterprise",
        description="Partner with customers. Python, AWS and Kubernetes experience required.",
    )

    assert bars.check(job, _profile(), "Python, AWS, Kubernetes, Postgres") != []


def test_a_real_sales_engineer_is_not_barred_from_sales_engineering() -> None:
    """**The control, and the reason this reads the résumé at all.**

    A rule keyed on the posting alone is a keyword blocklist. It would look like a
    precision fix and would deny a sales engineer every job they are qualified for —
    the same mistake `_language` and `_eligibility` are shaped to avoid.
    """
    job = _job(title="Sales Engineer Enterprise", description="Partner with customers.")

    assert bars.check(job, _profile(), "Sales Engineer at Acme. Carried a quota.") == []


def test_the_family_is_read_off_the_title_and_never_off_the_description() -> None:
    """ "Works closely with our solutions architects" appears in plenty of backend
    postings and says nothing about what the role is."""
    job = _job(
        title="Senior Backend Engineer",
        description="You will work closely with our solutions architects and support engineers.",
    )

    assert bars.check(job, _profile(), "Python, Go") == []


def test_the_engineering_titles_the_golden_set_calls_relevant_are_untouched() -> None:
    """**The measurement that decided this is a family check and not a craft taxonomy.**

    In the golden set `CLI Engineer`, `Data Engineer` and `Senior DevOps Engineer` are
    each `relevant` for one profile and `not_relevant` for another — identical titles,
    opposite labels — because the labeller judged skill depth against the posting body.
    A rule reading backend-vs-data-vs-infra off a title would have to get those wrong in
    one direction or the other, so this one does not try.
    """
    profile = _profile()
    for title in (
        "CLI Engineer",
        "Data Engineer",
        "Senior DevOps Engineer",
        "Senior Machine Learning Engineer",
        "Senior / Staff Product Engineer",
        "Systems Engineer, Data Intelligence & Analytics Team",
    ):
        assert bars.check(_job(title=title), profile, "Python") == [], title


# ---- timezone window: the category the model was carrying ------------------------

_PROXIFY = (
    "Located in the CET timezone (+/- 3 hours), we are unable to consider "
    "applications from candidates in other time zones."
)


def test_a_stated_timezone_window_bars_a_candidate_outside_it() -> None:
    """**Two of the reporting split's seven false positives, at 69 and 55.**

    `bars.py` could not compute this when model-quoted disqualifiers were demoted to
    advisory, so the category was knowingly given up. This is it coming back as a pure
    function of the pair.
    """
    job = _job(locations=["CET (+/- 3 hours)"], description=_PROXIFY)
    portland = _profile(locations=["Portland, Oregon"])

    assert bars.check(job, portland, "") != []


def test_the_same_posting_does_not_bar_a_candidate_inside_the_window() -> None:
    """**The control, and it is not hypothetical.**

    Four golden postings carry that sentence verbatim. Two are labelled `relevant` and two
    are not, and the sentence is identical in all four — only the candidate differs. A rule
    reading the posting alone would look like a precision fix and would delete both true
    positives.
    """
    job = _job(locations=["Time zone: CET (+/- 3 hours)"], description=_PROXIFY)
    krakow = _profile(locations=["Kraków"])

    assert bars.check(job, krakow, "") == []


def test_a_region_that_spans_the_window_is_not_barred() -> None:
    """A country is not a timezone. The US runs from -10 to -4, so "United States" settles
    nothing about a window at UTC-5 — and a range that overlaps at all must pass."""
    job = _job(locations=["EST (+/- 2 hours)"], description="EST (+/- 2 hours)")

    assert bars.check(job, _profile(locations=["Portland, Oregon"]), "") == []


def test_a_zone_without_a_tolerance_states_no_window() -> None:
    """ "We are a CET-based team" is not a rule. Inventing the tolerance would be this file
    guessing at how far either side the employer will actually go."""
    job = _job(description="We are a CET-based team and work core hours together.")

    assert bars.check(job, _profile(locations=["Portland, Oregon"]), "") == []


def test_a_profile_with_no_location_is_never_barred_on_time_zones() -> None:
    """Silence passes, as everywhere else here. `plain.txt` names no city at all."""
    job = _job(locations=["CET (+/- 3 hours)"], description=_PROXIFY)

    assert bars.check(job, _profile(locations=[]), "") == []
