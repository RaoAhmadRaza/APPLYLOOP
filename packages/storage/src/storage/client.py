"""Put, get, delete. Nothing else until something needs it.

Module functions rather than a class, matching how every other boundary in this repo is
expressed (`ADAPTERS` are modules, `record` is a function). There is one bucket; a
`StorageClient` with one instance would be an object to pass around for no benefit.

The client itself is built per call rather than cached at module scope. botocore
clients are not fork-safe, and Celery's prefork children would inherit one — the same
class of bug `db.session`'s NullPool exists to make impossible. Construction is local
work, no connection is opened until a request is made, and this path runs once per
résumé upload.
"""

import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from storage.settings import Settings, get_settings

# Object key prefix. Everything under one prefix so a lifecycle rule or a bulk delete
# can target résumés without touching M5's generated PDFs, which land next to them.
RESUME_PREFIX = "resumes"
DOCUMENT_PREFIX = "documents"


class StorageError(RuntimeError):
    """The blob store refused or could not be reached."""


def is_configured() -> bool:
    """The interlock. False means callers refuse cleanly instead of raising a 500."""
    settings = get_settings()
    return all(
        (
            settings.storage_endpoint_url,
            settings.storage_access_key_id,
            settings.storage_secret_access_key,
            settings.storage_bucket,
        )
    )


def build_key(profile_id: uuid.UUID, suffix: str) -> str:
    """`resumes/<profile_id>/<random>.pdf`.

    A fresh random component per upload rather than a fixed name, so re-uploading never
    overwrites the object a currently-running parse task is about to read. Old objects
    are left behind; a lifecycle rule reaps them, not this code.

    The suffix stays on the key because it is the only format hint the extractor gets —
    the key is all `profiles.resume_url` stores.
    """
    return f"{RESUME_PREFIX}/{profile_id}/{uuid.uuid4().hex}{suffix}"


def build_document_key(match_id: uuid.UUID, kind: str, version: int, suffix: str) -> str:
    """`documents/<match_id>/<kind>-v<version>-<random>.pdf`.

    Keyed by match rather than by profile because that is what `documents` rows are keyed
    by, and because it makes "everything generated for this application" one prefix.

    `version` is in the name as well as on the row so that an object cannot be silently
    replaced by a later render of the same document — same reasoning as `build_key`'s
    random component, one level up. The random component stays for the same reason it
    exists there: two renders of the same version must not collide on a key a reader is
    mid-fetch on.
    """
    return f"{DOCUMENT_PREFIX}/{match_id}/{kind}-v{version}-{uuid.uuid4().hex}{suffix}"


def put(key: str, data: bytes, content_type: str) -> str:
    """Store bytes under `key`. Returns the key, so callers can write it straight to a row."""
    settings = _require()
    try:
        _client(settings).put_object(
            Bucket=settings.storage_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError) as error:
        raise StorageError(f"could not store {key}") from error
    return key


def get(key: str) -> bytes:
    settings = _require()
    try:
        response = _client(settings).get_object(Bucket=settings.storage_bucket, Key=key)
        body: bytes = response["Body"].read()
    except (BotoCoreError, ClientError) as error:
        raise StorageError(f"could not read {key}") from error
    return body


def delete(key: str) -> None:
    settings = _require()
    try:
        _client(settings).delete_object(Bucket=settings.storage_bucket, Key=key)
    except (BotoCoreError, ClientError) as error:
        raise StorageError(f"could not delete {key}") from error


def _require() -> Settings:
    if not is_configured():
        raise StorageError("object storage is not configured")
    return get_settings()


def _client(settings: Settings) -> Any:
    assert settings.storage_access_key_id is not None  # noqa: S101 — _require checked
    assert settings.storage_secret_access_key is not None  # noqa: S101
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.storage_secret_access_key.get_secret_value(),
        region_name=settings.storage_region,
    )
