"""Guards the one mechanism that keeps Python enums and DB CHECK constraints aligned.

If someone adds a member to a StrEnum and forgets the migration, `check_in` still
generates the right SQL — but the deployed constraint is stale. This test asserts the
generator itself is faithful; `test_constraints.py` asserts the deployed constraint
actually rejects bad values.
"""

from db.constraints import check_in, check_in_or_null
from schemas.enums import (
    ApplicationStatus,
    ApplyMethod,
    ApprovalChannel,
    ApprovalDecision,
    AtsType,
    CompanyStatus,
    DocumentType,
    MatchLabel,
    MatchStatus,
    Seniority,
    UserPlan,
    WorkAuth,
)

ALL_ENUMS = [
    ApplicationStatus,
    ApplyMethod,
    ApprovalChannel,
    ApprovalDecision,
    AtsType,
    CompanyStatus,
    DocumentType,
    MatchLabel,
    MatchStatus,
    Seniority,
    UserPlan,
    WorkAuth,
]


def test_check_in_lists_every_member() -> None:
    for enum in ALL_ENUMS:
        # Arrange / Act
        sql = str(check_in("col", enum, name="col").sqltext)
        # Assert
        for member in enum:
            assert f"'{member.value}'" in sql, f"{enum.__name__}.{member.name} missing"


def test_check_in_lists_nothing_extra() -> None:
    for enum in ALL_ENUMS:
        sql = str(check_in("col", enum, name="col").sqltext)
        quoted = sql.split("IN (", 1)[1].rstrip(")")
        listed = {v.strip().strip("'") for v in quoted.split(",")}
        assert listed == {m.value for m in enum}, f"{enum.__name__} drifted"


def test_nullable_variant_permits_null() -> None:
    sql = str(check_in_or_null("col", MatchStatus, name="col").sqltext)
    assert "col IS NULL OR" in sql


def test_match_status_covers_the_documented_state_machine() -> None:
    """CLAUDE.md §6.1 names these six states. A rename here breaks every worker's
    conditional UPDATE, so it should break this test first."""
    assert {m.value for m in MatchStatus} == {
        "discovered",
        "tailored",
        "queued",
        "approved",
        "applied",
        "skipped",
    }


def test_apply_method_matches_the_data_boundary() -> None:
    """§5.2: the apply fork is a `method` value, not a class hierarchy."""
    assert {m.value for m in ApplyMethod} == {"agent", "extension", "manual"}
