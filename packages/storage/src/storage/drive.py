"""The Google Drive mirror. `documents.gdrive_url` is what this fills in.

The mirror exists because users expect their documents in their own Drive, not because
the pipeline needs a second copy — R2 is the durable one. That ordering is the whole
error policy: a mirror failure is recorded and the document still ships, because the
blob is already safe and a user with a working PDF and no Drive link has lost nothing
they will notice today.

**This uploads as the user, not as a service account, and that is forced rather than
chosen.** A service account has had a 0 GB Drive quota since 2023, so it cannot own a file:
Google's guidance is that it must write into a *Shared Drive* or act on behalf of a human
over OAuth. Shared Drives are a Google Workspace feature and do not exist on a personal
account, which is what this project runs on — so the documents land in the user's own Drive,
against their own storage, owned by them.

The scope is `drive.file`, the narrowest one that can upload: it grants access to files
**this application created** and to nothing else in the Drive. A folder made by hand in the
web UI was not created by this application, so `scripts/gdrive_token.py` creates the
destination folder during authorisation. That is why the folder id comes from the script
rather than from a URL.

`google-auth` and nothing else. The full `google-api-python-client` is a large dependency
tail for one multipart POST, and the upload protocol here is a documented HTTP call rather
than something worth a client library.
"""

import json
import uuid
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from storage.settings import get_settings

UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"

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
    """The interlock, same shape as the S3 client's. All four or nothing."""
    settings = get_settings()
    return all(
        (
            settings.gdrive_folder_id,
            settings.gdrive_client_id,
            settings.gdrive_client_secret,
            settings.gdrive_refresh_token,
        )
    )


def upload(*, name: str, data: bytes, content_type: str) -> str:
    """Upload one file into the configured folder. Returns its view link."""
    settings = get_settings()
    if not is_configured():
        raise DriveError("google drive is not configured")

    body, boundary = _related(
        metadata={"name": name, "parents": [settings.gdrive_folder_id]},
        data=data,
        content_type=content_type,
    )

    session = AuthorizedSession(_credentials(settings))
    try:
        response = session.post(
            UPLOAD_URL,
            params={
                "uploadType": "multipart",
                # Harmless on a personal Drive and required the day this points at a
                # Shared Drive instead; omitting it there 404s on the parent folder
                # rather than reporting a permission problem.
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


def _credentials(settings: Any) -> Credentials:
    """User credentials from a stored refresh token.

    No access token is held: `AuthorizedSession` mints one from the refresh token on first
    use and again whenever it expires, so nothing short-lived is ever persisted. The
    refresh token itself is the only secret, and it lives in `.env` (Part 13 rule 9).
    """
    assert settings.gdrive_client_secret is not None  # noqa: S101 - is_configured checked
    assert settings.gdrive_refresh_token is not None  # noqa: S101 - is_configured checked
    return Credentials(
        token=None,
        refresh_token=settings.gdrive_refresh_token.get_secret_value(),
        client_id=settings.gdrive_client_id,
        client_secret=settings.gdrive_client_secret.get_secret_value(),
        token_uri=TOKEN_URL,
        scopes=list(SCOPES),
    )


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
