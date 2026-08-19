"""The upload endpoint, through the real app.

Object storage and the broker are the two things faked, because neither is a *stage* —
Part 10's "no mocking of other stages" is about not mocking `jobs` or `matches`, and
these are an external bucket and a message queue. Everything the endpoint decides
(validation, the key, the row it writes, the task it fires) is real.
"""

from typing import Any
from uuid import uuid4

import pytest
import storage
from api import queue
from httpx import AsyncClient


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """An in-memory stand-in for R2. The real thing has its own live suite."""
    objects: dict[str, bytes] = {}

    def put(key: str, data: bytes, _content_type: str) -> str:
        objects[key] = data
        return key

    monkeypatch.setattr(storage, "is_configured", lambda: True)
    monkeypatch.setattr(storage, "put", put)
    return objects


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    sent: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(queue, "enqueue", lambda name, *args: sent.append((name, args)))
    return sent


async def _profile(client: AsyncClient) -> dict[str, Any]:
    # example.com, not example.test: EmailStr rejects reserved TLDs, so the .test
    # addresses the ORM-level suites use would 422 through the API.
    user = await client.post(
        "/users", json={"email": f"{uuid4().hex}@example.com", "auth_id": uuid4().hex}
    )
    profile = await client.post("/profiles", json={"user_id": user.json()["id"]})
    body: dict[str, Any] = profile.json()
    return body


async def test_an_upload_stores_the_file_and_records_the_key(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 200
    key = response.json()["resume_url"]
    assert key.startswith(f"resumes/{profile['id']}/")
    assert key.endswith(".pdf")
    assert bucket[key] == b"%PDF-1.4 fake"


async def test_the_parse_task_is_enqueued_by_name(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    """By dotted string, never by import: the API depends on celery the library and
    never on the `workers` package, which is what keeps pandas and markitdown out of
    everything this app imports."""
    profile = await _profile(client)

    await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert enqueued == [("workers.tasks.profiles.parse_profile", (profile["id"],))]


async def test_uploading_clears_the_previous_resume_text(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    """Between this request and the parse finishing, stale text left on the row would be
    scored by M4 against a document the user has just replaced."""
    profile = await _profile(client)
    await client.patch(f"/profiles/{profile['id']}", json={"master_resume": "old text"})

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.json()["master_resume"] is None


@pytest.mark.parametrize("filename", ["cv.pages", "cv.doc", "cv", "cv.exe"])
async def test_an_unreadable_format_is_refused_at_the_boundary(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any], filename: str
) -> None:
    """Rejected here with a clear message rather than an hour later in a worker log
    nobody is reading."""
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": (filename, b"data", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert enqueued == []


async def test_an_oversize_upload_is_refused(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    """The cap exists so a huge upload cannot exhaust the API process before anything
    has had a chance to reject it."""
    profile = await _profile(client)
    limit = storage.get_settings().max_resume_bytes

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"x" * (limit + 1), "application/pdf")},
    )

    assert response.status_code == 413
    assert enqueued == []


async def test_an_empty_upload_is_refused(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400


async def test_an_unknown_profile_is_a_404_not_a_stored_orphan(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    response = await client.post(
        f"/profiles/{uuid4()}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 404
    assert bucket == {}


async def test_unconfigured_storage_is_a_503_not_a_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, enqueued: list[Any]
) -> None:
    """A checkout with no bucket is a supported state everywhere else in this repo. It
    should read as "this deployment cannot do that yet", not as a crash."""
    monkeypatch.setattr(storage, "is_configured", lambda: False)
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 503
    assert enqueued == []


async def test_a_storage_failure_is_a_502_and_leaves_the_row_untouched(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, enqueued: list[Any]
) -> None:
    """No key on the row means no parse task chasing an object that was never written."""
    monkeypatch.setattr(storage, "is_configured", lambda: True)

    def refuse(*_args: Any, **_kwargs: Any) -> str:
        raise storage.StorageError("bucket on fire")

    monkeypatch.setattr(storage, "put", refuse)
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 502
    assert enqueued == []
    assert (await client.get(f"/profiles/{profile['id']}")).json()["resume_url"] is None


async def test_the_upload_route_is_not_shadowed_by_the_crud_router(
    client: AsyncClient, bucket: dict[str, bytes], enqueued: list[Any]
) -> None:
    """Both mount /profiles. If the CRUD router were registered first, /{row_id} would
    swallow the literal /resume path and this would 404 or 405."""
    profile = await _profile(client)

    response = await client.post(
        f"/profiles/{profile['id']}/resume",
        files={"file": ("cv.txt", b"Ada Lovelace", "text/plain")},
    )

    assert response.status_code == 200


# ---- parse status --------------------------------------------------------------------


async def test_parse_status_with_no_events_is_not_failed(client: AsyncClient) -> None:
    profile = await _profile(client)

    response = await client.get(f"/profiles/{profile['id']}/parse-status")

    assert response.status_code == 200
    assert response.json() == {"failed": False, "error": None, "at": None}


async def test_parse_status_surfaces_the_latest_failure(client: AsyncClient) -> None:
    """`profile.parse_failed` is what M3's worker writes on a real per-profile parse
    error (§3.7) — that record surfacing here is the only way this screen can tell
    "never uploaded" apart from "uploaded, and the parse blew up"."""
    profile = await _profile(client)
    await client.post(
        "/events",
        json={
            "type": "profile.parse_failed",
            "user_id": profile["user_id"],
            "payload_json": {"error": "the model returned nothing extractable"},
        },
    )

    response = await client.get(f"/profiles/{profile['id']}/parse-status")

    body = response.json()
    assert body["failed"] is True
    assert body["error"] == "the model returned nothing extractable"
    assert body["at"] is not None


async def test_parse_status_ignores_the_global_no_key_skip(client: AsyncClient) -> None:
    """`profile.parse_skipped` (no LLM key configured) carries no user_id — it is a
    global interlock, not a per-profile failure, and must never be read as one."""
    profile = await _profile(client)
    await client.post(
        "/events",
        json={"type": "profile.parse_skipped", "payload_json": {"reason": "no key"}},
    )

    response = await client.get(f"/profiles/{profile['id']}/parse-status")

    assert response.json()["failed"] is False


async def test_parse_status_for_an_unknown_profile_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/profiles/{uuid4()}/parse-status")

    assert response.status_code == 404


# ---- the manual match trigger ---------------------------------------------------------


async def test_match_enqueues_the_registered_task_name(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    profile = await _profile(client)

    response = await client.post(f"/profiles/{profile['id']}/match")

    assert response.status_code == 202
    assert enqueued == [("workers.tasks.matching.match_profile", (profile["id"],))]


async def test_matching_an_unknown_profile_enqueues_nothing(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    response = await client.post(f"/profiles/{uuid4()}/match")

    assert response.status_code == 404
    assert enqueued == []


async def _job_for(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/jobs",
        json={
            "source": "greenhouse",
            "external_id": f"acme:{uuid4().hex}",
            "title": "Some Role",
            "company": "Acme",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "raw_json": {},
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _match_for(
    client: AsyncClient, user_id: str, job_id: str, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        "/matches", json={"user_id": user_id, "job_id": job_id, "score": 100, **overrides}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_triggering_a_match_retires_stale_discovered_matches(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    """The bug this closes: a résumé swapped for a genuinely different person left the
    old person's `discovered` matches sitting at their old score forever, because
    nothing had asked the matcher to reconsider them. Retiring them to `skipped`
    before the fresh run is safe — match.py's own upsert (`_OVERWRITABLE`) already
    treats `skipped` as freely overwritable, so anything still relevant comes right
    back with a fresh score; only what the new run doesn't reconsider stays retired."""
    profile = await _profile(client)
    job = await _job_for(client)
    stale = await _match_for(client, profile["user_id"], job["id"], status="discovered")

    await client.post(f"/profiles/{profile['id']}/match")

    refreshed = (await client.get(f"/matches/{stale['id']}")).json()
    assert refreshed["status"] == "skipped"


async def test_triggering_a_match_never_touches_real_progress(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    """`tailored`/`queued`/`approved`/`applied` are real work, not a stale score —
    only `discovered` gets retired."""
    profile = await _profile(client)
    protected_statuses = ["tailored", "queued", "approved", "applied"]
    protected = []
    for match_status in protected_statuses:
        job = await _job_for(client)
        protected.append(
            await _match_for(client, profile["user_id"], job["id"], status=match_status)
        )

    await client.post(f"/profiles/{profile['id']}/match")

    for match_status, match in zip(protected_statuses, protected, strict=True):
        refreshed = (await client.get(f"/matches/{match['id']}")).json()
        assert refreshed["status"] == match_status


async def test_triggering_a_match_for_an_unknown_profile_touches_no_matches(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    profile = await _profile(client)
    job = await _job_for(client)
    stale = await _match_for(client, profile["user_id"], job["id"], status="discovered")

    response = await client.post(f"/profiles/{uuid4()}/match")

    assert response.status_code == 404
    unchanged = (await client.get(f"/matches/{stale['id']}")).json()
    assert unchanged["status"] == "discovered"


# ---- suggested preferences -------------------------------------------------------------


async def test_suggest_prefs_enqueues_the_registered_task_name(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    profile = await _profile(client)

    response = await client.post(f"/profiles/{profile['id']}/suggest-prefs")

    assert response.status_code == 202
    assert enqueued == [("workers.tasks.profiles.suggest_prefs", (profile["id"],))]


async def test_suggesting_prefs_for_an_unknown_profile_enqueues_nothing(
    client: AsyncClient, enqueued: list[Any]
) -> None:
    response = await client.post(f"/profiles/{uuid4()}/suggest-prefs")

    assert response.status_code == 404
    assert enqueued == []


async def test_suggested_prefs_with_no_events_is_all_empty(client: AsyncClient) -> None:
    profile = await _profile(client)

    response = await client.get(f"/profiles/{profile['id']}/suggested-prefs")

    assert response.status_code == 200
    assert response.json() == {
        "titles": [],
        "locations": [],
        "remote_modes": [],
        "must_have_keywords": [],
        "exclude_keywords": [],
        "at": None,
    }


async def test_suggested_prefs_surfaces_the_latest_suggestion(client: AsyncClient) -> None:
    """Never auto-saved to `prefs_json` — this is what the dashboard reads to pre-fill
    the form, and the user's own PATCH (untouched by this feature) is still what
    actually persists a choice."""
    profile = await _profile(client)
    await client.post(
        "/events",
        json={
            "type": "profile.prefs_suggested",
            "user_id": profile["user_id"],
            "payload_json": {
                "titles": ["Senior Backend Engineer"],
                "locations": ["Portland, Oregon"],
                "remote_modes": ["remote"],
                "must_have_keywords": ["Python", "PostgreSQL"],
                "exclude_keywords": [],
            },
        },
    )

    response = await client.get(f"/profiles/{profile['id']}/suggested-prefs")

    body = response.json()
    assert body["titles"] == ["Senior Backend Engineer"]
    assert body["must_have_keywords"] == ["Python", "PostgreSQL"]
    assert body["at"] is not None

    # Never written anywhere near prefs_json — a fresh profile's is still the bare
    # default, not backfilled with the suggestion.
    saved = (await client.get(f"/profiles/{profile['id']}")).json()
    assert saved["prefs_json"] == {}


async def test_suggested_prefs_for_an_unknown_profile_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/profiles/{uuid4()}/suggested-prefs")

    assert response.status_code == 404
