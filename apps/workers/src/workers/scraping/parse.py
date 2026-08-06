"""Cross-adapter value coercion.

Six providers, six date formats. Rather than six near-identical parsers, one function
that accepts every shape they actually emit — verified against live payloads:

    greenhouse       "2026-07-30T06:59:38-04:00"     ISO with offset
    lever            1553186035299                   epoch milliseconds
    ashby            "2024-03-04T14:29:08.532+00:00" ISO with fractional seconds
    workable         "2026-02-12"                    date only, no time, no zone
    smartrecruiters  "2026-06-24T10:00:11.853Z"      ISO with Z
    recruitee        ISO string
"""

from datetime import UTC, datetime
from typing import Any

# Lever is the only provider using epoch milliseconds.
_MILLIS_PER_SECOND = 1000


def to_utc(value: Any) -> datetime | None:
    """Coerce whatever a board called a timestamp into an aware UTC datetime.

    Returns None rather than raising on an unparseable value: a posting with a strange
    date is still a posting, and `posted_at` is nullable precisely for this.
    """
    if value is None or value == "":
        return None

    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / _MILLIS_PER_SECOND, tz=UTC)

    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None

    # A date-only value ("2026-02-12") parses to midnight naive. Treat it as UTC rather
    # than dropping it — the day is the information, the hour was never there.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def join_nonempty(parts: list[str | None], sep: str = ", ") -> str | None:
    """Join the parts that exist. Returns None when nothing does."""
    return sep.join(p for p in parts if p) or None
