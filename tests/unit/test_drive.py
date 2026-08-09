"""The Drive mirror's real logic, offline.

The upload itself needs live credentials and is proved by `make verify-live-drive`. What is
checkable for free is the body it builds — `multipart/related`, which is *not* what
`requests` produces from `files=` and looks identical in a debugger — the interlock, and
the shape of the credentials.
"""

import json

import pytest
from pydantic import SecretStr
from storage import drive
from storage.settings import Settings


def test_the_body_is_multipart_related_not_form_data() -> None:
    """Drive's multipart upload rejects `multipart/form-data`.

    The two differ only in the content type and the absence of `Content-Disposition`
    headers, which is exactly why reaching for `requests`' `files=` is the natural
    mistake and produces a 400 that reads like a permissions problem.
    """
    body, boundary = drive._related(
        metadata={"name": "a.pdf", "parents": ["folder"]},
        data=b"%PDF-1.7 bytes",
        content_type="application/pdf",
    )

    assert f"--{boundary}".encode() in body
    assert body.endswith(f"--{boundary}--\r\n".encode())
    assert b"Content-Type: application/json; charset=UTF-8" in body
    assert b"Content-Type: application/pdf" in body
    assert b"%PDF-1.7 bytes" in body
    assert b"Content-Disposition" not in body, "that is form-data, which Drive refuses"

    metadata = json.loads(body.split(b"\r\n\r\n")[1].split(b"\r\n--")[0])
    assert metadata == {"name": "a.pdf", "parents": ["folder"]}


def test_binary_content_survives_the_body_intact() -> None:
    """The PDF is spliced in as bytes, never decoded. A mirror that corrupts the file it
    copies is worse than one that fails."""
    payload = bytes(range(256))

    body, _ = drive._related(
        metadata={"name": "a.pdf"}, data=payload, content_type="application/pdf"
    )

    assert payload in body


def test_an_unconfigured_mirror_refuses_rather_than_guesses() -> None:
    """The same interlock shape as the S3 client and the LLM client."""
    assert drive.is_configured() is False

    with pytest.raises(drive.DriveError, match="not configured"):
        drive.upload(name="a.pdf", data=b"x", content_type="application/pdf")


def test_the_credentials_hold_no_access_token() -> None:
    """The mirror runs as the user, over OAuth, because a service account has a 0 GB Drive
    quota and Shared Drives are a Workspace feature this project does not have.

    Only the refresh token is stored. The access token is minted per session and never
    persisted, so there is one long-lived secret rather than two.
    """
    settings = Settings(
        gdrive_folder_id="folder",
        gdrive_client_id="client",
        gdrive_client_secret=SecretStr("secret-not-real"),
        gdrive_refresh_token=SecretStr("refresh-not-real"),
    )

    credentials = drive._credentials(settings)

    assert credentials.token is None, "an access token was stored; it should be minted"
    assert credentials.refresh_token == "refresh-not-real"
    assert list(credentials.scopes) == ["https://www.googleapis.com/auth/drive.file"]
