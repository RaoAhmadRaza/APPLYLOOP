"""Generates CHECK constraints from the enums in `packages/schemas`.

This is the mechanism that keeps the Python enum and the DB constraint from drifting
apart: there is exactly one list of legal values, and the SQL is derived from it.
`tests/unit/test_enums_match_checks.py` asserts the derivation still holds.
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint


def check_in(column: str, enum: type[StrEnum], *, name: str) -> CheckConstraint:
    """`CHECK (column IN (...))` built from every member of `enum`.

    `name` is bare (e.g. "status"); the metadata naming convention expands it to
    `ck_<table>_<name>`.
    """
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


def check_in_or_null(column: str, enum: type[StrEnum], *, name: str) -> CheckConstraint:
    """Same, for a nullable column. `NULL IN (...)` is NULL, not false, so a plain
    IN check would pass anyway — but stating it makes the intent readable in psql."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({values})", name=name)
