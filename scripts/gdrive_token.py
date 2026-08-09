"""One-time: authorise APPLYLOOP against your own Google Drive and print the two settings.

    uv run python scripts/gdrive_token.py --client-id ... --client-secret ...

Opens a browser, waits for you to approve, then creates the destination folder and prints
`GDRIVE_FOLDER_ID` and `GDRIVE_REFRESH_TOKEN` ready to paste into `.env`.

**Why this exists rather than a service account.** A service account has had a 0 GB Drive
quota since 2023, so it cannot own a file; Google's own guidance is that it must either
write into a *Shared Drive* or act on behalf of a human over OAuth. Shared Drives are a
Google Workspace feature and are not available on a personal account, which is what this
project uses — so the mirror runs as the user, and the documents land in their own Drive
against their own storage.

**Why the script creates the folder.** The scope is `drive.file`, the narrowest one that
can upload: it grants access to files *this application created* and to nothing else in the
Drive. A folder made by hand in the web UI was not created by this application, so writing
into it would need the far broader `drive` scope. Creating it here keeps the scope narrow
and costs one API call.

Nothing is written to disk. The refresh token is printed once and belongs in `.env`, which
is gitignored — Part 13 rule 9.
"""

import argparse
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

import requests

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
FILES = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"

# Loopback on a fixed port, because the OAuth client's redirect URI has to match exactly
# and a "Desktop app" client accepts any http://localhost port.
PORT = 8765
REDIRECT = f"http://localhost:{PORT}"

FOLDER_NAME = "APPLYLOOP documents"


class _Catch(http.server.BaseHTTPRequestHandler):
    """Receives the one redirect Google makes back to us."""

    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catch.code = (query.get("code") or [None])[0]
        _Catch.state = (query.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"APPLYLOOP is authorised. Close this tab and go back to the terminal."
            if _Catch.code
            else b"No authorisation code came back. Check the terminal."
        )

    def log_message(self, *_: object) -> None:
        """Silence the default request logging; the script does its own talking."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    args = parser.parse_args()

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": args.client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        # Both are required to be *given* a refresh token: offline asks for one, and
        # consent forces the prompt even if you have approved this client before —
        # without it Google silently returns an access token and no refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer(("localhost", PORT), _Catch)

    print(f"\nOpening your browser. If it does not open, paste this:\n\n  {url}\n")
    # Opened from a thread so the browser launch cannot race the listener being ready.
    threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    # Blocks until exactly one request arrives, which is the redirect. No loop, no poll.
    server.handle_request()

    if _Catch.code is None:
        raise SystemExit("no authorisation code came back — nothing was changed")
    if _Catch.state != state:
        raise SystemExit("state did not match the request; refusing the code")

    granted = requests.post(
        TOKEN,
        data={
            "code": _Catch.code,
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    granted.raise_for_status()
    payload = granted.json()

    refresh = payload.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "Google returned no refresh token. That happens when this client has been "
            "approved before — revoke it at myaccount.google.com/permissions and re-run."
        )

    folder = requests.post(
        FILES,
        headers={"Authorization": f"Bearer {payload['access_token']}"},
        json={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        timeout=30,
    )
    folder.raise_for_status()

    print("\nPaste these into .env:\n")
    print(f"GDRIVE_FOLDER_ID={folder.json()['id']}")
    print(f"GDRIVE_CLIENT_ID={args.client_id}")
    print(f"GDRIVE_CLIENT_SECRET={args.client_secret}")
    print(f"GDRIVE_REFRESH_TOKEN={refresh}")
    print(f"\nThe folder '{FOLDER_NAME}' is in your Drive. Then: make verify-live-drive\n")


if __name__ == "__main__":
    main()
