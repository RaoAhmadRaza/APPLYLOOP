"""Object storage, against a real S3-compatible bucket.

    APPLYLOOP_LIVE_STORAGE=1 uv run pytest tests/integration/test_storage_live.py

Not in CI: it needs credentials CI does not have, and a build must not go red because a
bucket had a bad afternoon.

Small on purpose. Everything the *endpoint* decides is covered offline in
test_profile_upload.py against an in-memory bucket; what cannot be faked is whether the
SigV4 signature, the endpoint URL and the "auto" region actually satisfy R2 — which is
exactly the class of thing that works in every test and fails on first deploy.
"""

import os
import uuid

import pytest
import storage

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("APPLYLOOP_LIVE_STORAGE"),
        reason="writes to a real bucket; set APPLYLOOP_LIVE_STORAGE=1",
    ),
    pytest.mark.skipif(
        not os.getenv("STORAGE_BUCKET"),
        reason="object storage is unconfigured, which is a supported state by design",
    ),
]


@pytest.fixture(autouse=True)
def _fresh_settings() -> None:
    storage.get_settings.cache_clear()


def test_a_resume_round_trips_through_the_bucket() -> None:
    """Put, get, delete. The whole surface, because there is no more of it."""
    key = storage.build_key(uuid.uuid4(), ".pdf")
    payload = b"%PDF-1.4 live round trip"

    assert storage.put(key, payload, "application/pdf") == key
    try:
        assert storage.get(key) == payload
    finally:
        storage.delete(key)


def test_reading_a_missing_object_raises_rather_than_returning_empty() -> None:
    """An empty read would become an empty résumé, which reads as a person with no
    experience rather than as a failure."""
    with pytest.raises(storage.StorageError):
        storage.get(f"resumes/{uuid.uuid4()}/does-not-exist.pdf")


def test_two_uploads_for_one_profile_do_not_collide() -> None:
    """`build_key` carries a random component so re-uploading cannot overwrite the
    object a currently-running parse task is about to read."""
    profile_id = uuid.uuid4()

    first = storage.build_key(profile_id, ".pdf")
    second = storage.build_key(profile_id, ".pdf")

    assert first != second
    assert first.startswith(f"resumes/{profile_id}/")
