"""M4's free rung against a real Postgres: seed a pool, filter it, assert what survived.

No model, no network — `filters.py` has neither. What is under test is the polarity of
five predicates, and **every polarity test asserts that a specific row survives rather
than that a count is non-zero**. That distinction is the whole point: a filter that drops
everything and a filter that drops nothing both produce a plausible number, and M3's
post-mortem is about a unit test that passed while asserting the wrong behaviour.

The keyword cases use strings measured off the real pool rather than invented ones. On
13,725 open jobs, `description ILIKE '%go%'` matched 85% of them.
"""

import uuid
from typing import Any

import pytest
from db.models import Job, Match, Profile, User
from schemas.enums import MatchStatus, RemoteMode, Seniority, WorkAuth
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy.orm import Session
from workers.matching import filters


def _user(session: Session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", auth_id=str(uuid.uuid4()))
    session.add(user)
    session.flush()
    return user


def _profile(session: Session, **columns: Any) -> ProfileRead:
    profile = Profile(user_id=_user(session).id, parsed_json={}, prefs_json={}, **columns)
    session.add(profile)
    session.flush()
    return ProfileRead.model_validate(profile)


def _job(session: Session, **columns: Any) -> Job:
    defaults: dict[str, Any] = {
        "source": "greenhouse",
        "external_id": f"acme:{uuid.uuid4()}",
        "title": "Software Engineer",
        "company": "Acme",
        "locations": [],
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "raw_json": {},
    }
    job = Job(**{**defaults, **columns})
    session.add(job)
    session.flush()
    return job


def _kept(session: Session, profile: ProfileRead, prefs: Prefs | None = None) -> set[uuid.UUID]:
    ids, _ = filters.candidates(session, profile, prefs or Prefs())
    return set(ids)


# ---- the deduped pool ------------------------------------------------------------


def test_only_the_deduped_pool_is_considered(session: Session) -> None:
    """`closed_at IS NULL AND canonical_id IS NULL` — §6.3, written once, here."""
    profile = _profile(session)
    open_job = _job(session)
    closed = _job(session, closed_at="2026-01-01T00:00:00Z")
    survivor = _job(session)
    loser = _job(session, canonical_id=survivor.id)

    kept = _kept(session, profile)

    assert open_job.id in kept
    assert closed.id not in kept
    assert loser.id not in kept


def test_a_job_this_user_already_scored_is_not_scored_again(session: Session) -> None:
    """The cost control that makes a run over an unchanged pool free."""
    profile = _profile(session)
    scored = _job(session)
    fresh = _job(session)
    session.add(Match(user_id=profile.user_id, job_id=scored.id, score=70))
    session.flush()

    ids, funnel = filters.candidates(session, profile, Prefs())

    assert set(ids) == {fresh.id}
    assert funnel.already_scored == 1


def test_a_match_whose_explain_failed_is_retried(session: Session) -> None:
    """Score NULL means the LLM call failed, not that the job was judged and rejected.

    Excluding it would make one bad afternoon permanent for that (user, job) pair.
    """
    profile = _profile(session)
    job = _job(session)
    session.add(
        Match(user_id=profile.user_id, job_id=job.id, score=None, status=MatchStatus.SKIPPED)
    )
    session.flush()

    assert job.id in _kept(session, profile)


# ---- polarity law 1: unknown location never drops --------------------------------


def test_a_job_naming_no_place_survives_a_profile_that_names_one(session: Session) -> None:
    profile = _profile(session, locations=["Portland, OR"])
    silent = _job(session, locations=[])

    assert silent.id in _kept(session, profile)


def test_a_profile_naming_no_place_keeps_every_job(session: Session) -> None:
    profile = _profile(session, locations=[])
    anywhere = _job(session, locations=["Reykjavik, Iceland"])

    assert anywhere.id in _kept(session, profile)


def test_location_matches_on_a_case_folded_segment_not_an_exact_array_overlap(
    session: Session,
) -> None:
    """The measured reason this filter is coarse.

    On the real pool `profiles.locations && jobs.locations` for "San Francisco, CA"
    matched 141 rows; the segment form matched 808. The sources spell the same city
    five ways and the résumé spells it a sixth.
    """
    profile = _profile(session, locations=["San Francisco, CA"])
    spelled_differently = _job(session, locations=["San Francisco"])
    lower_cased = _job(session, locations=["san francisco, ca"])
    with_a_country = _job(session, locations=["San Francisco, United States"])
    elsewhere = _job(session, locations=["Lagos, Nigeria"])

    kept = _kept(session, profile)

    assert spelled_differently.id in kept
    assert lower_cased.id in kept
    assert with_a_country.id in kept
    assert elsewhere.id not in kept


def test_a_remote_job_survives_a_city_the_profile_never_named(session: Session) -> None:
    """§3.5's filter is "location/remote" — two things. A remote job in a city the
    person never listed is still a job they can do."""
    profile = _profile(session, locations=["Portland, OR"])
    remote_elsewhere = _job(
        session, locations=["New York, NY"], remote_mode=RemoteMode.REMOTE.value
    )

    assert remote_elsewhere.id in _kept(session, profile)


def test_a_remote_job_is_dropped_for_someone_who_will_not_take_remote(
    session: Session,
) -> None:
    """The remote escape hatch is scoped to people who actually want it."""
    profile = _profile(session, locations=["Portland, OR"])
    remote_elsewhere = _job(
        session, locations=["New York, NY"], remote_mode=RemoteMode.REMOTE.value
    )

    kept = _kept(session, profile, Prefs(remote_modes=[RemoteMode.ONSITE]))

    assert remote_elsewhere.id not in kept


# ---- polarity law 2: NULL remote_mode never drops --------------------------------


def test_a_job_whose_source_did_not_state_a_remote_mode_survives(session: Session) -> None:
    """87% of the real pool. Dropping these for a remote-preferring user removes every
    lever and greenhouse posting — the entire ATS layer M1 exists to produce."""
    profile = _profile(session)
    unstated = _job(session, remote_mode=None)

    kept = _kept(session, profile, Prefs(remote_modes=[RemoteMode.REMOTE]))

    assert unstated.id in kept


def test_an_empty_remote_preference_means_no_preference_not_no_jobs(
    session: Session,
) -> None:
    """`x = ANY('{}')` is false for every x. Written as a bare `= ANY`, the default
    preference would return nothing at all — for every user, on every run."""
    profile = _profile(session)
    onsite = _job(session, remote_mode=RemoteMode.ONSITE.value)
    hybrid = _job(session, remote_mode=RemoteMode.HYBRID.value)
    unstated = _job(session, remote_mode=None)

    kept = _kept(session, profile, Prefs(remote_modes=[]))

    assert {onsite.id, hybrid.id, unstated.id} <= kept


def test_a_stated_remote_mode_outside_the_preference_is_the_only_drop(
    session: Session,
) -> None:
    profile = _profile(session)
    onsite = _job(session, remote_mode=RemoteMode.ONSITE.value)

    kept = _kept(session, profile, Prefs(remote_modes=[RemoteMode.REMOTE]))

    assert onsite.id not in kept


# ---- polarity law 3: an unbanded title never drops -------------------------------


def test_a_title_stating_no_seniority_survives(session: Session) -> None:
    """Two-thirds of real postings. "Software Engineer" carries no band, and reading
    that as a mismatch drops most of the pool for everyone."""
    profile = _profile(session, seniority=Seniority.SENIOR)
    plain = _job(session, title="Software Engineer")

    assert plain.id in _kept(session, profile)


def test_a_profile_with_no_band_keeps_every_title(session: Session) -> None:
    profile = _profile(session, seniority=None)
    internship = _job(session, title="Software Engineering Intern")

    assert internship.id in _kept(session, profile)


@pytest.mark.parametrize(
    ("title", "survives"),
    [
        ("Staff Software Engineer", True),  # one band up — a REACH, not a mismatch
        ("Engineering Manager", True),  # lead, a peer of staff
        ("Software Engineer", True),  # unbanded
        ("Senior Backend Engineer", True),  # exactly the band
        ("Software Engineering Intern", False),  # three bands down
        ("Director of Engineering", False),  # three bands up
    ],
)
def test_seniority_is_a_window_not_a_floor(session: Session, title: str, survives: bool) -> None:
    """A senior engineer should see neither an internship nor a director role.

    A floor would show them every director posting in the pool; an equality would drop
    the reach roles the label vocabulary exists to describe.
    """
    profile = _profile(session, seniority=Seniority.SENIOR)
    job = _job(session, title=title)

    assert (job.id in _kept(session, profile)) is survives


# ---- polarity law 4: a silent posting never drops on work auth -------------------


def test_a_posting_silent_about_sponsorship_survives(session: Session) -> None:
    profile = _profile(session, work_auth=WorkAuth.NEEDS_SPONSORSHIP)
    silent = _job(session, description="We are hiring a backend engineer.")

    assert silent.id in _kept(session, profile)


def test_a_posting_refusing_sponsorship_is_dropped_for_someone_who_needs_it(
    session: Session,
) -> None:
    """§7.2 calls this the single most-praised feature in the leading product, and its
    whole value is not showing someone jobs they cannot legally take."""
    profile = _profile(session, work_auth=WorkAuth.NEEDS_SPONSORSHIP)
    refuses = _job(session, description="We are unable to sponsor visas for this role.")

    assert refuses.id not in _kept(session, profile)


@pytest.mark.parametrize(
    "work_auth", [WorkAuth.CITIZEN, WorkAuth.PERMANENT_RESIDENT, WorkAuth.VISA_HOLDER, None]
)
def test_the_sponsorship_clause_is_never_consulted_for_anyone_else(
    session: Session, work_auth: WorkAuth | None
) -> None:
    """Someone who can already work there is not blocked by a sponsorship sentence."""
    profile = _profile(session, work_auth=work_auth)
    refuses = _job(session, description="We are unable to sponsor visas for this role.")

    assert refuses.id in _kept(session, profile)


# ---- polarity law 5: keywords are words, not substrings --------------------------


def test_a_must_have_keyword_matches_a_word_not_a_substring(session: Session) -> None:
    """Measured: `ILIKE '%go%'` matched 1,234 of 1,458 open jobs; `\\mgo\\M` matched 136.

    "good", "going", "Google", "category", "Diego" — a substring must-have filter drops
    nothing at all, which silently disables §3.5's free rung.
    """
    profile = _profile(session)
    real = _job(session, description="Five years of experience with Go and Kubernetes.")
    good = _job(session, description="You are a good candidate for a fast-moving team.")
    google = _job(session, description="Experience with Google Cloud Platform.")

    kept = _kept(session, profile, Prefs(must_have_keywords=["Go"]))

    assert real.id in kept
    assert good.id not in kept
    assert google.id not in kept


def test_java_does_not_match_javascript(session: Session) -> None:
    profile = _profile(session)
    java = _job(session, description="Strong Java and Spring Boot experience.")
    javascript = _job(session, description="Strong JavaScript and React experience.")

    kept = _kept(session, profile, Prefs(must_have_keywords=["Java"]))

    assert java.id in kept
    assert javascript.id not in kept


def test_an_exclude_keyword_drops_only_a_whole_word_match(session: Session) -> None:
    """The mirror image, and the more dangerous direction: `exclude=["go"]` as a
    substring would drop 85% of the pool and still look like a working filter."""
    profile = _profile(session)
    unwanted = _job(session, title="Senior Sales Engineer", description="Own the sales cycle.")
    wanted = _job(session, title="Backend Engineer", description="Own the wholesale ledger.")

    kept = _kept(session, profile, Prefs(exclude_keywords=["sales"]))

    assert unwanted.id not in kept
    assert wanted.id in kept


def test_a_keyword_is_matched_against_the_title_as_well_as_the_description(
    session: Session,
) -> None:
    """Feeds routinely ship a one-line description; the title is the only signal there."""
    profile = _profile(session)
    titled = _job(session, title="Rust Engineer", description="Join us.")

    assert titled.id in _kept(session, profile, Prefs(must_have_keywords=["Rust"]))


def test_a_keyword_containing_regex_metacharacters_is_taken_literally(
    session: Session,
) -> None:
    """`C++` is a valid thing to want and an invalid regex. Unescaped it raises from
    inside the SELECT, which is a 500 on a run rather than a message at the boundary."""
    profile = _profile(session)
    job = _job(session, description="Deep C++ and systems programming experience.")

    assert job.id in _kept(session, profile, Prefs(must_have_keywords=["C++"]))


def test_every_must_have_keyword_is_required_not_just_one(session: Session) -> None:
    profile = _profile(session)
    both = _job(session, description="We use Python and Kubernetes daily.")
    only_one = _job(session, description="We use Python daily.")

    kept = _kept(session, profile, Prefs(must_have_keywords=["Python", "Kubernetes"]))

    assert both.id in kept
    assert only_one.id not in kept


# ---- the funnel ------------------------------------------------------------------


def test_the_funnel_accounts_for_every_row_in_the_pool(session: Session) -> None:
    """§3.7 applied to filters: a stage that quietly drops everything raises nothing.

    The counts must sum to the pool or the funnel cannot be used to tell "the filter
    worked" from "the filter is a WHERE false".
    """
    profile = _profile(session, locations=["Portland, OR"], seniority=Seniority.SENIOR)
    _job(session, locations=["Lagos, Nigeria"])
    _job(session, title="Software Engineering Intern", locations=["Portland, OR"])
    _job(session, locations=["Portland, OR"])

    _, funnel = filters.candidates(session, profile, Prefs(remote_modes=[RemoteMode.ONSITE]))

    dropped = (
        funnel.dropped_location
        + funnel.dropped_remote
        + funnel.dropped_seniority
        + funnel.dropped_work_auth
        + funnel.dropped_keywords
    )
    assert funnel.pool == dropped + funnel.already_scored + funnel.candidates
    assert funnel.candidates == 1


def test_a_job_failing_two_filters_is_counted_once(session: Session) -> None:
    """Or the funnel double-counts and stops summing to the pool."""
    profile = _profile(session, locations=["Portland, OR"], seniority=Seniority.SENIOR)
    _job(session, title="Director of Engineering", locations=["Lagos, Nigeria"])

    _, funnel = filters.candidates(session, profile, Prefs())

    assert funnel.dropped_location + funnel.dropped_seniority == 1


def test_the_funnel_has_no_salary_counter(session: Session) -> None:
    """§3.5 lists salary as a hard filter, but `jobs` carries no salary column.

    A counter for a filter that structurally cannot fire reads, in a dashboard, exactly
    like a filter that ran and dropped nothing.
    """
    assert not hasattr(filters.Funnel, "dropped_salary")
    assert "dropped_salary" not in filters.Funnel(*[0] * 8).as_payload()
