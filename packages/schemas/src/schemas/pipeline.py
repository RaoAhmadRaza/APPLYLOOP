"""The dashboard's read model — one row per scored match, with the job attached.

Every other collection in this API is `make_crud_router`'s generic list, which takes
`limit` and `offset` and nothing else. That is right for a table and wrong for a screen:
`matches` carries `job_id` and not the job's title, so "this user's matches, best first"
would fetch every match and then fetch each job one at a time — around eighty round trips
for the first screen of a demo.

So this is a purpose-built shape rather than a CRUD row, and it is the only one. It is
also the place the deduped-pool rule is enforced for readers (§6.3): a closed job or a
dedupe loser is not something the dashboard should have to know to filter out.
"""

import uuid
from datetime import datetime

from schemas.common import Schema


class PipelineJob(Schema):
    """The posting, as much of it as a list row needs. No description — it runs to
    thousands of characters and no list screen shows it."""

    id: uuid.UUID
    title: str
    company: str
    location: str | None
    remote_mode: str | None
    url: str | None
    ats_type: str | None
    posted_at: datetime | None


class PipelineDocument(Schema):
    id: uuid.UUID
    type: str
    version: int
    gdrive_url: str | None


class PipelineRow(Schema):
    """One match, ready to render.

    `reasons_json` is passed through whole. Its partitions are what the match-detail
    screen reads, and re-declaring them here would be a second copy of `MatchReasons` to
    keep in step with the one M4 writes.
    """

    match_id: uuid.UUID
    score: int | None
    label: str | None
    status: str
    created_at: datetime
    reasons_json: dict
    job: PipelineJob
    documents: list[PipelineDocument]
    # From the latest `tailor.generated` event, absent until the match is tailored. How
    # much of the posting's own vocabulary the tailored résumé carries — reported beside
    # the score because it is the number tailoring can actually move.
    keywords_matched: int | None = None
    keywords_total: int | None = None
