"""The paging loop the three paginated feeds share.

Stops on a 429 rather than raising. Raising hands the failure to Celery's
`autoretry_for`, which re-runs the *whole* pass against the host that just asked us to
slow down — the opposite of what a 429 means. Keeping the pages we already have is safe
because every paginated feed is `COMPLETE = False`, so a short pass never closes a row.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx


def paged(
    client: httpx.Client,
    page_url: Callable[[int], str],
    extract: Callable[[Any], list[dict[str, Any]]],
    pages: int,
    delay: float,
) -> list[dict[str, Any]]:
    """Fetch up to `pages`, stopping early on an empty page or a 429.

    `page_url` takes a 0-based page index; each feed maps it to whatever its API calls
    a page — an offset, a 1-based number, a cursor.
    """
    postings: list[dict[str, Any]] = []
    for page in range(pages):
        response = client.get(page_url(page))
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            break
        response.raise_for_status()
        batch = extract(response.json())
        if not batch:
            break
        postings.extend(batch)
        if delay and page + 1 < pages:
            time.sleep(delay)
    return postings
