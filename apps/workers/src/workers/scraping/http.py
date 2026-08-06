"""The one HTTP client every adapter uses.

Sync, not async: Celery is the concurrency here — one task per board — so asyncio never
enters the worker and the sync `NullPool` session design stays intact.

No proxy. Part 13 rule 12: the ATS layer doesn't need one and it costs real money. Only
M2's aggregator layer does.

No retry logic either. Celery's `autoretry_for=(httpx.HTTPError,)` on the task already
gives exponential backoff with jitter, so a helper here would be a second, worse one.
"""

import httpx

# Long enough for a slow board, short enough that one dead host can't pin a worker.
TIMEOUT_SECONDS = 20.0

# Identify honestly. These are public endpoints and we are not pretending otherwise.
USER_AGENT = "applyloop/0.1 (+https://github.com/RaoAhmadRaza/APPLYLOOP)"


def client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        # Workable's documented host 302s to apply.workable.com, and several careers
        # pages redirect before revealing their board.
        follow_redirects=True,
    )
