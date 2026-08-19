"""The four endpoints the dashboard runs on, through the real app and a real database.

Object storage and the broker are faked, for the reason `test_profile_upload.py` gives:
neither is a *stage*, so Part 10's "no mocking of other stages" does not apply to them.
Every row, constraint and transition here is real.
"""

from typing import Any
from uuid import uuid4

import pytest
import storage
from api import queue
from httpx import AsyncClient
from sqlalchemy import Engine, text


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    sent: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(queue, "enqueue", lambda name, *args: sent.append((name, args)))
    return sent


async def _user(client: AsyncClient) -> str:
    response = await client.post(
        "/users", json={"email": f"{uuid4().hex}@example.com", "auth_id": uuid4().hex}
    )
    user_id: str = response.json()["id"]
    return user_id


async def _job(client: AsyncClient, title: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "source": "greenhouse",
        "external_id": f"acme:{uuid4().hex}",
        "title": title,
        "company": "Acme",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "raw_json": {},
        **overrides,
    }
    response = await client.post("/jobs", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _retire(engine: Engine, job_id: str, column: str, value: str) -> None:
    """Set a column the API deliberately does not expose.

    `closed_at` and `canonical_id` are absent from `JobCreate` *and* `JobUpdate` — they
    belong to the scraper and the deduper, and nothing should be able to close a job or
    declare it a duplicate over HTTP. So a test that needs one seeds it the way those
    stages do, in SQL. Writing them through the API is not merely inconvenient, it
    silently does nothing, which is how the first version of these two tests passed a
    filter that was working.
    """
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE jobs SET {column} = :value WHERE id = :id"),  # noqa: S608 — literal
            {"value": value, "id": job_id},
        )


async def _match(
    client: AsyncClient, user_id: str, job_id: str, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        "/matches", json={"user_id": user_id, "job_id": job_id, **overrides}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ---- the read model ----------------------------------------------------------------


async def test_the_pipeline_returns_matches_best_first_with_the_job_attached(
    client: AsyncClient,
) -> None:
    """The whole reason this endpoint exists: `matches` carries `job_id` and not the
    job's title, so the generic list would cost one request per row to render a screen."""
    user_id = await _user(client)
    for title, score in (("Low", 21), ("Best", 82), ("Middle", 55)):
        job = await _job(client, title)
        await _match(client, user_id, job["id"], score=score, status="discovered")

    response = await client.get(f"/users/{user_id}/pipeline")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [row["score"] for row in body["items"]] == [82, 55, 21]
    assert [row["job"]["title"] for row in body["items"]] == ["Best", "Middle", "Low"]
    assert body["items"][0]["job"]["company"] == "Acme"


async def test_a_closed_job_never_appears(client: AsyncClient, engine: Engine) -> None:
    """§6.3: `closed_at IS NULL` is the open pool. A match against a role that was taken
    down is history, and the dashboard should not have to know the column exists."""
    user_id = await _user(client)
    live = await _job(client, "Open")
    gone = await _job(client, "Closed")
    _retire(engine, gone["id"], "closed_at", "2026-08-01T00:00:00Z")
    await _match(client, user_id, live["id"], score=30)
    await _match(client, user_id, gone["id"], score=99)

    body = (await client.get(f"/users/{user_id}/pipeline")).json()

    assert [row["job"]["title"] for row in body["items"]] == ["Open"]
    assert body["total"] == 1


async def test_a_dedupe_loser_never_appears(client: AsyncClient, engine: Engine) -> None:
    """§6.3 again: `canonical_id IS NULL` means "this row is the survivor". The loser is
    marked rather than deleted, so a reader that forgot this shows the same job twice."""
    user_id = await _user(client)
    survivor = await _job(client, "Survivor")
    loser = await _job(client, "Loser")
    _retire(engine, loser["id"], "canonical_id", survivor["id"])
    await _match(client, user_id, survivor["id"], score=40)
    await _match(client, user_id, loser["id"], score=90)

    body = (await client.get(f"/users/{user_id}/pipeline")).json()

    assert [row["job"]["title"] for row in body["items"]] == ["Survivor"]


async def test_an_unscored_match_sorts_last_not_first(client: AsyncClient) -> None:
    """M4 leaves `score` NULL when the posting stated no requirements. "We could not
    judge this" must not outrank a genuine 82."""
    user_id = await _user(client)
    unscored = await _job(client, "Unjudged")
    scored = await _job(client, "Judged")
    await _match(client, user_id, unscored["id"])
    await _match(client, user_id, scored["id"], score=82)

    body = (await client.get(f"/users/{user_id}/pipeline")).json()

    assert [row["job"]["title"] for row in body["items"]] == ["Judged", "Unjudged"]


async def test_the_status_filter_narrows_both_the_page_and_the_total(client: AsyncClient) -> None:
    user_id = await _user(client)
    for status_value in ("discovered", "tailored", "skipped"):
        job = await _job(client, status_value)
        await _match(client, user_id, job["id"], score=50, status=status_value)

    body = (await client.get(f"/users/{user_id}/pipeline?status=tailored")).json()

    assert body["total"] == 1
    assert body["items"][0]["status"] == "tailored"


async def test_one_user_never_sees_another_users_matches(client: AsyncClient) -> None:
    """Multi-tenancy is application-level filtering for now (Part 14, open). That makes
    this the only thing enforcing it, which is a reason to test it rather than assume."""
    mine = await _user(client)
    yours = await _user(client)
    job = await _job(client, "Shared posting")
    await _match(client, mine, job["id"], score=10)
    other = await _job(client, "Theirs")
    await _match(client, yours, other["id"], score=90)

    body = (await client.get(f"/users/{mine}/pipeline")).json()

    assert body["total"] == 1
    assert body["items"][0]["job"]["title"] == "Shared posting"


# ---- transitions -------------------------------------------------------------------


async def test_approve_moves_a_tailored_match_through_queued(client: AsyncClient) -> None:
    """§6.1 says nothing may skip a transition, and `queued` sits between the two. With
    no notifier writing it, the endpoint writes both or M6 inherits a hole."""
    user_id = await _user(client)
    job = await _job(client, "Approvable")
    match = await _match(client, user_id, job["id"], score=82, status="tailored")

    response = await client.post(f"/matches/{match['id']}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_approving_twice_is_a_no_op_and_not_an_error(client: AsyncClient) -> None:
    """§3.4: a double-tap is expected, not exceptional."""
    user_id = await _user(client)
    job = await _job(client, "Approvable")
    match = await _match(client, user_id, job["id"], score=82, status="tailored")

    first = await client.post(f"/matches/{match['id']}/approve")
    second = await client.post(f"/matches/{match['id']}/approve")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "approved"


async def test_approving_something_never_tailored_is_a_conflict(client: AsyncClient) -> None:
    """There is nothing to approve: the documents do not exist yet."""
    user_id = await _user(client)
    job = await _job(client, "Not ready")
    match = await _match(client, user_id, job["id"], score=82, status="discovered")

    response = await client.post(f"/matches/{match['id']}/approve")

    assert response.status_code == 409
    assert "tailored" in response.json()["detail"]


async def test_skip_is_legal_from_discovered_and_is_idempotent(client: AsyncClient) -> None:
    user_id = await _user(client)
    job = await _job(client, "Unwanted")
    match = await _match(client, user_id, job["id"], score=21, status="discovered")

    first = await client.post(f"/matches/{match['id']}/skip")
    second = await client.post(f"/matches/{match['id']}/skip")

    assert first.json()["status"] == "skipped"
    assert second.status_code == 200
    assert second.json()["status"] == "skipped"


async def test_a_skipped_match_cannot_then_be_approved(client: AsyncClient) -> None:
    """ "Skip excludes it from every apply path" is the M6 gate clause, and this is where
    it holds — `skipped` is not a state approve accepts."""
    user_id = await _user(client)
    job = await _job(client, "Unwanted")
    match = await _match(client, user_id, job["id"], score=21, status="tailored")

    await client.post(f"/matches/{match['id']}/skip")
    response = await client.post(f"/matches/{match['id']}/approve")

    assert response.status_code == 409


async def test_an_applied_match_cannot_be_skipped(client: AsyncClient) -> None:
    """The one thing `skipped` cannot undo is an application that already went out."""
    user_id = await _user(client)
    job = await _job(client, "Gone out")
    match = await _match(client, user_id, job["id"], score=82, status="applied")

    response = await client.post(f"/matches/{match['id']}/skip")

    assert response.status_code == 409


async def test_transitions_on_an_unknown_match_are_404(client: AsyncClient) -> None:
    assert (await client.post(f"/matches/{uuid4()}/approve")).status_code == 404
    assert (await client.post(f"/matches/{uuid4()}/skip")).status_code == 404


# ---- the tailor trigger ------------------------------------------------------------


async def test_tailor_enqueues_the_registered_task_name(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    """The dotted string is the contract with the worker, and a typo in it fails on the
    broker rather than at import. This assertion is what keeps them in step."""
    user_id = await _user(client)
    job = await _job(client, "Tailorable")
    match = await _match(client, user_id, job["id"], score=82, status="discovered")

    response = await client.post(f"/matches/{match['id']}/tailor")

    assert response.status_code == 202
    assert enqueued == [("workers.tasks.tailoring.tailor_match", (match["id"],))]


async def test_tailoring_an_unknown_match_enqueues_nothing(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    response = await client.post(f"/matches/{uuid4()}/tailor")

    assert response.status_code == 404
    assert enqueued == []


# ---- the document link -------------------------------------------------------------


async def _document(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    user_id = await _user(client)
    job = await _job(client, "Tailored")
    match = await _match(client, user_id, job["id"], score=82, status="tailored")
    response = await client.post(
        "/documents",
        json={
            "match_id": match["id"],
            "type": "resume",
            "storage_url": f"documents/{match['id']}/resume-v1.pdf",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_download_redirects_to_a_signed_url(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        storage,
        "presigned_get",
        lambda key, expires_in: f"https://bucket.test/{key}?X-Amz-Signature=abc",
    )
    document = await _document(client)

    response = await client.get(f"/documents/{document['id']}/download")

    assert response.status_code == 307
    assert "X-Amz-Signature" in response.headers["location"]


async def test_the_drive_mirror_is_the_fallback_when_storage_is_unconfigured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfigured storage is a supported state everywhere else here."""
    monkeypatch.setattr(storage, "is_configured", lambda: False)
    document = await _document(client, gdrive_url="https://drive.google.com/file/d/abc/view")

    response = await client.get(f"/documents/{document['id']}/download")

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://drive.google.com/")


async def test_no_storage_and_no_mirror_is_a_503_not_a_crash(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "is_configured", lambda: False)
    document = await _document(client)

    response = await client.get(f"/documents/{document['id']}/download")

    assert response.status_code == 503


async def test_downloading_an_unknown_document_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"/documents/{uuid4()}/download")).status_code == 404


# ---- tailoring counters on a row ----------------------------------------------------


async def _event(
    client: AsyncClient, event_type: str, payload: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/events", json={"type": event_type, "payload_json": payload, "user_id": user_id}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_generated_row_carries_the_full_tailoring_report(client: AsyncClient) -> None:
    """Every counter `tailor.generated` writes, not just the keyword pair — this is
    what Screen 4's side panel reads."""
    user_id = await _user(client)
    job = await _job(client, "Tailored")
    match = await _match(client, user_id, job["id"], score=82, status="tailored")
    await _event(
        client,
        "tailor.generated",
        {
            "match_id": match["id"],
            "keywords_matched": 3,
            "keywords_total": 5,
            "bullets_kept": 4,
            "bullets_stripped": 1,
            "skills_kept": 6,
            "fabricated_skills": 0,
        },
    )

    body = (await client.get(f"/users/{user_id}/pipeline")).json()
    row = body["items"][0]

    assert row["keywords_matched"] == 3
    assert row["keywords_total"] == 5
    assert row["bullets_kept"] == 4
    assert row["bullets_stripped"] == 1
    assert row["skills_kept"] == 6
    assert row["fabricated_skills"] == 0
    assert row["generated_at"] is not None


async def test_a_blocked_row_carries_the_reason_and_no_counters(client: AsyncClient) -> None:
    user_id = await _user(client)
    job = await _job(client, "Blocked")
    match = await _match(client, user_id, job["id"], score=82, status="discovered")
    await _event(
        client,
        "tailor.blocked",
        {"match_id": match["id"], "reason": "100% of bullets were untraceable, ceiling 30%"},
    )

    body = (await client.get(f"/users/{user_id}/pipeline")).json()
    row = body["items"][0]

    assert row["blocked_reason"] == "100% of bullets were untraceable, ceiling 30%"
    assert row["keywords_matched"] is None
    assert row["bullets_kept"] is None


async def test_a_later_retry_clears_an_earlier_block(client: AsyncClient) -> None:
    """A block is a finished, rejected attempt, not permanent — a later success must
    replace it outright, not sit beside it."""
    user_id = await _user(client)
    job = await _job(client, "Retried")
    match = await _match(client, user_id, job["id"], score=82, status="tailored")
    await _event(client, "tailor.blocked", {"match_id": match["id"], "reason": "first try failed"})
    await _event(
        client,
        "tailor.generated",
        {
            "match_id": match["id"],
            "keywords_matched": 2,
            "keywords_total": 4,
            "bullets_kept": 3,
            "bullets_stripped": 0,
            "skills_kept": 5,
            "fabricated_skills": 0,
        },
    )

    body = (await client.get(f"/users/{user_id}/pipeline")).json()
    row = body["items"][0]

    assert row["blocked_reason"] is None
    assert row["keywords_matched"] == 2


# ---- the funnel summary --------------------------------------------------------------


async def test_pipeline_summary_reads_the_latest_match_scored_event(client: AsyncClient) -> None:
    user_id = await _user(client)
    await _event(
        client,
        "match.scored",
        {
            "candidates": 952,
            "embedded_new": 952,
            "shortlisted": 40,
            "above_threshold": 12,
            "skipped_below": 28,
        },
        user_id=user_id,
    )

    body = (await client.get(f"/users/{user_id}/pipeline/summary")).json()

    assert body["candidates"] == 952
    assert body["above_threshold"] == 12
    assert body["skipped_below"] == 28
    assert body["last_run_at"] is not None


async def test_pipeline_summary_with_no_runs_returns_nulls(client: AsyncClient) -> None:
    user_id = await _user(client)

    body = (await client.get(f"/users/{user_id}/pipeline/summary")).json()

    assert body["last_run_at"] is None
    assert body["candidates"] is None


async def test_pipeline_summary_ignores_another_users_run(client: AsyncClient) -> None:
    mine = await _user(client)
    yours = await _user(client)
    await _event(client, "match.scored", {"candidates": 10}, user_id=yours)

    body = (await client.get(f"/users/{mine}/pipeline/summary")).json()

    assert body["candidates"] is None
