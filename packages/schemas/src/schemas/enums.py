"""Bounded string sets — the single source of truth for every DB CHECK constraint.

These are `StrEnum`, never `sqlalchemy.Enum`. A native Postgres ENUM cannot `ADD VALUE`
inside the transaction Alembic wraps migrations in, and removing a value rewrites the
table. `TEXT` + a named `CHECK` built from these lists is drop-and-re-add instead.

`db.constraints.check_in()` generates the CHECK SQL from these members, and
`tests/unit/test_enums_match_checks.py` asserts the two never drift apart.
"""

from enum import StrEnum


class AtsType(StrEnum):
    """CLAUDE.md §4.1 Layer 1 — the six direct-JSON ATS providers."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKABLE = "workable"
    SMARTRECRUITERS = "smartrecruiters"
    RECRUITEE = "recruitee"
    OTHER = "other"


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"  # board 404'd for N runs — §4.3 auto-retire
    ERROR = "error"


class UserPlan(StrEnum):
    FREE = "free"
    PRO = "pro"


class WorkAuth(StrEnum):
    CITIZEN = "citizen"
    PERMANENT_RESIDENT = "permanent_resident"
    VISA_HOLDER = "visa_holder"
    NEEDS_SPONSORSHIP = "needs_sponsorship"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    DIRECTOR = "director"


class MatchStatus(StrEnum):
    """The pipeline state machine — CLAUDE.md §6.1.

    discovered -> tailored -> queued -> approved -> applied,
    with skipped reachable from any state.

    Transitions are enforced by each worker's conditional
    `UPDATE ... WHERE status = <expected>` plus a rowcount check, which doubles as
    optimistic concurrency for Celery retries. No DB trigger until a non-worker
    writer starts touching this column.
    """

    DISCOVERED = "discovered"
    TAILORED = "tailored"
    QUEUED = "queued"
    APPROVED = "approved"
    APPLIED = "applied"
    SKIPPED = "skipped"


class MatchLabel(StrEnum):
    GOOD_FIT = "good_fit"
    FAIR = "fair"
    REACH = "reach"


class DocumentType(StrEnum):
    RESUME = "resume"
    COVER_LETTER = "cover_letter"


class ApplyMethod(StrEnum):
    """CLAUDE.md §5.2 — a data boundary, not a class hierarchy.

    Both apply paths write to `applications` with a different `method`. There is no
    ApplyStrategy ABC and there should never be one.
    """

    AGENT = "agent"
    EXTENSION = "extension"
    MANUAL = "manual"


class ApplicationStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FAILED = "failed"
    # 'blocked' arrives at M10 with the validator gate — one ALTER on the CHECK,
    # which is exactly why this is TEXT and not a native enum.


class ApprovalChannel(StrEnum):
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    # 'whatsapp' arrives at M11, after Telegram is proven end to end (§7.5).


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    SKIPPED = "skipped"
