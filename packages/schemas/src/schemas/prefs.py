"""`profiles.prefs_json` — the half of the M4 hard filter that has no column.

§3.5 names five hard filters: location/remote, seniority band, work authorisation,
salary floor, must-have keywords. Four of them already have promoted columns on
`profiles` (`locations[]`, `seniority`, `work_auth`, `salary_floor`); `remote` and
`must_have_keywords` do not.

**The split rule, stated once:** anything M4 filters in SQL is a column; everything else
lives here. build-sequence.md §M3 lists "location, remote, salary floor, must-haves" as
*prefs*, which would duplicate two columns — that reading is rejected, because two
places to write one value is two places for them to disagree, and the columns are the
ones with the GIN index and the CHECK constraints.

This is a schema rather than a free-form dict so `prefs_json` is a contract M4 can rely
on. It is not stored as columns because none of it is filtered in SQL yet. §6.2 says
adding a column later is "fine"; changing one's meaning is not. Trigger to promote:
M4's filter query needs an index on one of these.
"""

from pydantic import Field

from schemas.common import Schema
from schemas.enums import RemoteMode


class Prefs(Schema):
    # Empty means "no preference", NOT "no remote". An empty list must never be read as
    # a filter that excludes everything — same polarity rule as `RemoteMode` being NULL
    # on a job whose source did not say.
    remote_modes: list[RemoteMode] = Field(default_factory=list)

    # A job must mention all of these. §3.5's "must-have keywords" — free, and it runs
    # before anything that costs money.
    must_have_keywords: list[str] = Field(default_factory=list)

    # ...and none of these. Not in §3.5's list, but it is the same `WHERE` clause read
    # backwards and it is what stops a Python-developer search returning sales roles.
    exclude_keywords: list[str] = Field(default_factory=list)

    # Target roles in the user's own words. M4 embeds these alongside the résumé: what
    # someone wants next is often not what their last job title says.
    titles: list[str] = Field(default_factory=list)


class PrefsSuggestion(Schema):
    """A model's best guess at starting values for the form above, from a résumé that
    was just parsed. Never auto-saved — `GET /profiles/{id}/suggested-prefs` only
    fills the dashboard's inputs; the user's own `PATCH` (unchanged) is still what
    writes `prefs_json`. Deliberately not `Prefs` itself: `titles` here means "what to
    search for", not a claim about the candidate, so it may rephrase toward a target
    role in a way `parsed_json` extraction never may (§3.3 is about claims shown to an
    employer; this is a search filter the candidate can freely edit or ignore).
    """

    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_modes: list[RemoteMode] = Field(default_factory=list)
    # An AND filter (`Prefs.must_have_keywords`'s own docstring: "a job must mention
    # all of these"). Capped at 1 in the schema, not just the prompt — a model that
    # "helpfully" lists 3 real skills together over-constrains the search to almost
    # nothing, since few postings happen to name all three. The validator is the
    # guardrail here, same reasoning as §3.3's fabrication check: a prompt asking
    # nicely is not enough to trust alone.
    must_have_keywords: list[str] = Field(default_factory=list, max_length=1)
    exclude_keywords: list[str] = Field(default_factory=list)
