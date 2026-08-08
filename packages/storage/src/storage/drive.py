"""The Google Drive mirror. `documents.gdrive_url` is what this fills in.

The mirror exists because users expect their documents in their own Drive, not because
the pipeline needs a second copy — R2 is the durable one. That ordering is the whole
error policy: a mirror failure is recorded and the document still ships, because the
blob is already safe and a user with a working PDF and no Drive link has lost nothing
they will notice today.

**The folder must live in a Shared Drive.** Service accounts have had a 0 GB My Drive
quota since 2023: uploading to a service account's own Drive fails `storageQuotaExceeded`
even on a completely empty account, and no exception is available. Create a Shared Drive,
share the folder with the service account's address, and put its id in `GDRIVE_FOLDER_ID`.

`google-auth` and nothing else. The full `google-api-python-client` is a large dependency
tail for one multipart POST, and the upload protocol here is a documented HTTP call rather
than something worth a client library.
"""

import base64
import binascii
import json
import uuid
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from storage.settings import get_settings

UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

# `drive.file` rather than `drive`: it grants access to files this application created,
# and nothing else in the Drive. The narrowest scope that can do the job.
SCOPES = ("https://www.googleapis.com/auth/drive.file",)

TIMEOUT_SECONDS = 60


class DriveError(RuntimeError):
    """The mirror failed.

    Deliberately **not** a `StorageError`. That one is wired into the tasks' retry
    policy, and a Drive outage must not re-run a whole tailoring job — the model call is
    the expensive part and it has already succeeded by the time this runs.
    """


def is_configured() -> bool:
    """The interlock, same shape as the S3 client's."""
    settings = get_settings()
    return bool(settings.gdrive_folder_id and settings.gdrive_service_account_json)


def upload(*, name: str, data: bytes, content_type: str) -> str:
    """Upload one file into the configured Shared Drive folder. Returns its view link."""
    settings = get_settings()
    if not is_configured():
        raise DriveError("google drive is not configured")

    assert settings.gdrive_service_account_json is not None  # noqa: S101 - is_configured
    body, boundary = _related(
        metadata={"name": name, "parents": [settings.gdrive_folder_id]},
        data=data,
        content_type=content_type,
    )

    session = AuthorizedSession(
        _credentials(settings.gdrive_service_account_json.get_secret_value())
    )
    try:
        response = session.post(
            UPLOAD_URL,
            params={
                "uploadType": "multipart",
                # Both are required for a Shared Drive, and omitting either produces a
                # 404 on the parent folder rather than a permission error.
                "supportsAllDrives": "true",
                "fields": "id,webViewLink",
            },
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as error:  # transport, auth refresh, DNS
        raise DriveError(f"drive upload failed: {type(error).__name__}") from error

    if response.status_code >= 400:
        # Bounded, and without the response body's echo of the file name — that name
        # contains a match id and the candidate's document type (Part 13 rule 9).
        raise DriveError(f"drive refused the upload: HTTP {response.status_code}")

    payload: dict[str, Any] = response.json()
    link = payload.get("webViewLink")
    if not link:
        # A 200 with no link means the file exists and we cannot point at it, which is
        # worse than a failure because a NULL column is at least honest.
        raise DriveError("drive accepted the upload but returned no link")
    return str(link)


def _credentials(encoded: str) -> service_account.Credentials:
    """Decode the base64 service-account key.

    Base64 rather than a mounted file: it is one environment variable, it survives
    docker-compose and a secret manager identically, and a JSON key with embedded
    newlines pasted into a `.env` is a well-known way to spend an afternoon.
    """
    try:
        info = json.loads(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError) as error:
        raise DriveError("GDRIVE_SERVICE_ACCOUNT_JSON is not base64-encoded JSON") from error
    return service_account.Credentials.from_service_account_info(info, scopes=list(SCOPES))


def _related(*, metadata: dict[str, Any], data: bytes, content_type: str) -> tuple[bytes, str]:
    """Build a `multipart/related` body.

    Hand-built because `requests`' `files=` produces `multipart/form-data`, which Drive's
    multipart upload rejects. The two look identical in a debugger and are not.
    """
    boundary = uuid.uuid4().hex
    marker = f"--{boundary}".encode()
    return (
        b"\r\n".join(
            [
                marker,
                b"Content-Type: application/json; charset=UTF-8",
                b"",
                json.dumps(metadata).encode(),
                marker,
                f"Content-Type: {content_type}".encode(),
                b"",
                data,
                f"--{boundary}--".encode(),
                b"",
            ]
        ),
        boundary,
    )
