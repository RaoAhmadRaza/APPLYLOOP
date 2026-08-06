"""M0 gate item 5: a pgvector similarity query returns.

Asserts the *known* nearest vector wins, not merely that some row came back — a query
that returns the wrong neighbour still "returns a result".
"""

import uuid

from db.models import Job, JobEmbedding
from schemas.job_embedding import EMBEDDING_DIM
from sqlalchemy import select
from sqlalchemy.orm import Session

MODEL = "test/fixture@1536"


def _unit_vector(hot_index: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[hot_index] = 1.0
    return v


def _make_job(session: Session, external_id: str) -> Job:
    job = Job(
        source="test",
        external_id=external_id,
        title=f"Engineer {external_id}",
        company="Acme",
        url=f"https://example.test/{external_id}",
        raw_json={},
    )
    session.add(job)
    session.flush()
    return job


def test_cosine_distance_returns_the_known_nearest(session: Session) -> None:
    # Arrange — three orthogonal unit vectors, one per job.
    jobs = [_make_job(session, f"vec-{i}") for i in range(3)]
    for i, job in enumerate(jobs):
        session.add(JobEmbedding(job_id=job.id, model=MODEL, embedding=_unit_vector(i)))
    session.flush()

    query = _unit_vector(1)  # exactly job[1]

    # Act
    nearest = session.scalars(
        select(JobEmbedding).order_by(JobEmbedding.embedding.cosine_distance(query)).limit(1)
    ).one()

    # Assert
    assert nearest.job_id == jobs[1].id


def test_distance_is_zero_for_an_identical_vector(session: Session) -> None:
    job = _make_job(session, "vec-identical")
    vector = _unit_vector(7)
    session.add(JobEmbedding(job_id=job.id, model=MODEL, embedding=vector))
    session.flush()

    distance = session.scalar(
        select(JobEmbedding.embedding.cosine_distance(vector)).where(JobEmbedding.job_id == job.id)
    )
    assert distance == 0.0


def test_one_job_can_hold_several_models(session: Session) -> None:
    """The whole reason embeddings are a separate table: dual-running two providers
    during a swap must not require touching `jobs`."""
    job = _make_job(session, "vec-multi")
    session.add(JobEmbedding(job_id=job.id, model="provider-a@1536", embedding=_unit_vector(0)))
    session.add(JobEmbedding(job_id=job.id, model="provider-b@1536", embedding=_unit_vector(1)))
    session.flush()

    rows = session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job.id)).all()
    assert {r.model for r in rows} == {"provider-a@1536", "provider-b@1536"}


def test_deleting_a_job_cascades_to_its_embeddings(session: Session) -> None:
    job = _make_job(session, "vec-cascade")
    session.add(JobEmbedding(job_id=job.id, model=MODEL, embedding=_unit_vector(3)))
    session.flush()
    job_id: uuid.UUID = job.id

    session.delete(job)
    session.flush()

    remaining = session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job_id)).all()
    assert remaining == []
