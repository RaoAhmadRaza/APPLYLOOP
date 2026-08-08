"""The Drive mirror's two pieces of real logic, offline.

The upload itself needs a Shared Drive and is proved by `make verify-live-drive`. What is
checkable for free is the body it builds — `multipart/related`, which is *not* what
`requests` produces from `files=` and looks identical in a debugger — and the interlock.
"""

import base64
import json

import pytest
from storage import drive


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


def test_a_key_that_is_not_base64_json_fails_with_a_sentence_a_human_can_act_on() -> None:
    """The most likely misconfiguration by a wide margin: pasting the raw JSON key.

    It fails on the variable's name and format, and says neither the key nor its contents
    — Part 13 rule 9 applies to error strings too.
    """
    with pytest.raises(drive.DriveError, match="GDRIVE_SERVICE_ACCOUNT_JSON"):
        drive._credentials(base64.b64encode(b"not json at all").decode())

    with pytest.raises(drive.DriveError, match="GDRIVE_SERVICE_ACCOUNT_JSON"):
        drive._credentials('{"type": "service_account"}')
