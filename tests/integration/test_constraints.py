"""Beyond the M0 gate: proves §3.4's idempotency guarantee is enforced by the
database, not by discipline.

Every assertion here is a duplicate that must be REFUSED. §3.4 says the applications
row is the lock, not the log, and Part 13 rule 10 forbids check-then-act in Python —
both are only true if these constraints actually exist and actually fire.
"""

import pytest
from db.models import Application, Approval, Company, Document, Job, Match, User
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _job(session: Session, external_id: str = "x-1", source: str = "greenhouse") -> Job:
    job = Job(
        source=source,
        external_id=external_id,
        title="Engineer",
        company="Acme",
        url="https://example.test/1",
        raw_json={},
    )
    session.add(job)
    session.flush()
    return job


def _user(session: Session, email: str = "u@example.test") -> User:
    user = User(email=email, auth_id=f"auth|{email}")
    session.add(user)
    session.flush()
    return user


def _match(session: Session) -> Match:
    match = Match(user_id=_user(session).id, job_id=_job(session).id)
    session.add(match)
    session.flush()
    return match


def test_applications_reject_a_duplicate_match_method(session: Session) -> None:
    """THE lock. A Celery retry, a worker restart, or a Telegram double-tap must not
    produce a second application."""
    match = _match(session)
    session.add(Application(match_id=match.id, method="extension"))
    session.flush()

    session.add(Application(match_id=match.id, method="extension"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_applications_allow_a_different_method_for_the_same_match(session: Session) -> None:
    """The constraint is (match_id, method), not match_id alone — the same match can
    legitimately be attempted by the agent and by the extension."""
    match = _match(session)
    session.add(Application(match_id=match.id, method="extension"))
    session.add(Application(match_id=match.id, method="manual"))
    session.flush()  # must not raise


def test_jobs_reject_a_duplicate_source_external_id(session: Session) -> None:
    """Without this, every M1 ingest run duplicates every job."""
    _job(session, "dupe")
    # Added directly rather than via _job(), which flushes internally — the flush is
    # the assertion here and must happen inside pytest.raises.
    session.add(
        Job(
            source="greenhouse",
            external_id="dupe",
            title="Engineer",
            company="Acme",
            url="https://example.test/2",
            raw_json={},
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_jobs_allow_the_same_external_id_from_a_different_source(session: Session) -> None:
    _job(session, "shared", source="greenhouse")
    _job(session, "shared", source="linkedin")
    session.flush()


def test_matches_reject_a_duplicate_user_job(session: Session) -> None:
    """The highest-severity constraint: without it a matcher re-run fans out duplicate
    matches, then duplicate documents, then duplicate approval messages to a person."""
    user, job = _user(session), _job(session)
    session.add(Match(user_id=user.id, job_id=job.id))
    session.flush()
    session.add(Match(user_id=user.id, job_id=job.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_companies_reject_a_duplicate_ats_slug(session: Session) -> None:
    """Two rows for one board would split the M1 change-detection state."""
    session.add(Company(name="Acme", ats_type="greenhouse", ats_slug="acme"))
    session.flush()
    session.add(Company(name="Acme Inc", ats_type="greenhouse", ats_slug="acme"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_documents_reject_a_duplicate_type_version(session: Session) -> None:
    match = _match(session)
    session.add(Document(match_id=match.id, type="resume", storage_url="s3://a", version=1))
    session.flush()
    session.add(Document(match_id=match.id, type="resume", storage_url="s3://b", version=1))
    with pytest.raises(IntegrityError):
        session.flush()


def test_documents_allow_a_new_version(session: Session) -> None:
    match = _match(session)
    session.add(Document(match_id=match.id, type="resume", storage_url="s3://a", version=1))
    session.add(Document(match_id=match.id, type="resume", storage_url="s3://b", version=2))
    session.flush()


def test_approvals_reject_a_second_pending_request(session: Session) -> None:
    """The partial unique index. This is what stops a retry of the M6 notify step
    double-messaging a real human."""
    match = _match(session)
    session.add(Approval(match_id=match.id, channel="telegram"))
    session.flush()
    session.add(Approval(match_id=match.id, channel="telegram"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_approvals_allow_a_new_request_once_the_first_is_decided(session: Session) -> None:
    """Partial, not total: history of decided requests is preserved, and a match can
    legitimately be re-asked after a decision."""
    match = _match(session)
    first = Approval(match_id=match.id, channel="telegram")
    session.add(first)
    session.flush()

    session.execute(
        text("UPDATE approvals SET decided_at = now(), decision = 'skipped' WHERE id = :id"),
        {"id": first.id},
    )
    session.add(Approval(match_id=match.id, channel="telegram"))
    session.flush()  # must not raise


def test_check_constraint_rejects_an_illegal_status_from_raw_sql(session: Session) -> None:
    """The DB is the last line of defence — this bypasses pydantic entirely, the way
    a bad migration or a manual psql session would."""
    match = _match(session)
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE matches SET status = 'definitely_not_a_state' WHERE id = :id"),
            {"id": match.id},
        )
        session.flush()


def test_check_constraint_rejects_an_out_of_range_score(session: Session) -> None:
    match = _match(session)
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE matches SET score = 101 WHERE id = :id"), {"id": match.id})
        session.flush()
