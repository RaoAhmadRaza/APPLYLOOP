"""M5's mirror clause, against a real Shared Drive. `make verify-live-drive`.

Not in CI: it needs credentials and it writes a file into somebody's Drive.

**This suite fails rather than skips when the opt-in variable is set and the credentials
are not.** Every other live target in this repo can exit 0 having run nothing — the
`skipif` reads `os.getenv` and `.env` is not loaded into pytest's process — and that is
a recorded defect, caught by reading output rather than by an exit code. An opt-in run
that finds no credentials is a mistake, not a supported state, so the new targets do not
inherit it.
"""

import os
import uuid

import pytest
from storage import drive

LIVE = os.getenv("APPLYLOOP_LIVE_DRIVE")

pytestmark = pytest.mark.skipif(
    not LIVE, reason="uploads to a real Shared Drive; set APPLYLOOP_LIVE_DRIVE=1"
)

# A one-page PDF, built by hand so the suite needs no renderer and no fixture file.
TINY_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def test_the_opt_in_is_honoured_or_the_run_fails() -> None:
    """The first assertion, deliberately.

    If `APPLYLOOP_LIVE_DRIVE=1` is set and the credentials are absent, this run must go
    red. A green run that uploaded nothing is the failure mode that hid behind every
    other live target here.
    """
    assert drive.is_configured(), (
        "APPLYLOOP_LIVE_DRIVE is set but GDRIVE_FOLDER_ID / GDRIVE_SERVICE_ACCOUNT_JSON "
        "are not in this process. Run `set -a; . ./.env; set +a` first — pytest does not "
        "load .env."
    )


def test_a_pdf_lands_in_the_shared_drive_and_comes_back_with_a_link() -> None:
    """The gate's fourth clause, the half a bucket cannot prove."""
    name = f"applyloop-verify-{uuid.uuid4().hex}.pdf"

    link = drive.upload(name=name, data=TINY_PDF, content_type="application/pdf")

    print(f"\n  mirrored: {link}")
    assert link.startswith("https://"), "a link M6 can put in a message"
    assert "drive.google.com" in link


def test_the_folder_is_a_shared_drive() -> None:
    """The single most likely misconfiguration, and it presents as a quota error.

    A service account's own Drive has had a 0 GB quota since 2023, so uploading to a
    folder that is not in a Shared Drive fails `storageQuotaExceeded` on a completely
    empty account. If this passes, the folder is set up correctly.
    """
    link = drive.upload(
        name=f"applyloop-quota-{uuid.uuid4().hex}.pdf",
        data=TINY_PDF,
        content_type="application/pdf",
    )

    assert link
