"""Six providers, six date formats. `to_utc` is the one place that knows all of them."""

from datetime import UTC, datetime

from workers.scraping.parse import join_nonempty, to_utc


def test_greenhouse_iso_with_offset() -> None:
    assert to_utc("2026-07-30T06:59:38-04:00") == datetime(2026, 7, 30, 10, 59, 38, tzinfo=UTC)


def test_smartrecruiters_z_suffix() -> None:
    assert to_utc("2026-06-24T10:00:11.853Z") == datetime(
        2026, 6, 24, 10, 0, 11, 853000, tzinfo=UTC
    )


def test_lever_epoch_milliseconds() -> None:
    """Lever is the only one using epoch ms. Reading it as seconds lands in 1970."""
    assert to_utc(1553186035299) == datetime(2019, 3, 21, 16, 33, 55, 299000, tzinfo=UTC)
    # Read as seconds it would land in 1970 — the failure this test exists to catch.
    assert to_utc(1553186035299).year != 1970  # type: ignore[union-attr]


def test_workable_date_only_becomes_utc_midnight() -> None:
    """`published_on` has no time and no zone. The day is the information."""
    assert to_utc("2026-02-12") == datetime(2026, 2, 12, 0, 0, tzinfo=UTC)


def test_unparseable_returns_none_rather_than_raising() -> None:
    """A posting with a strange date is still a posting; posted_at is nullable for this."""
    assert to_utc("last Tuesday") is None
    assert to_utc(None) is None
    assert to_utc("") is None


def test_booleans_are_not_treated_as_epochs() -> None:
    """bool subclasses int, so a naive isinstance check turns True into 1970-01-01."""
    assert to_utc(True) is None


def test_join_nonempty_skips_gaps() -> None:
    assert join_nonempty(["Athens", None, "Greece"]) == "Athens, Greece"
    assert join_nonempty([None, None]) is None
