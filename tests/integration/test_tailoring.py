"""M5's stage against a real Postgres: seed a match, run it, assert the row delta.

Part 10's rule — no mocking of other stages. There is nothing to mock: M3's vault is
`evidence` rows and M4's output is a `matches` row, so both are seeded as rows. The model
is the only thing faked, because a test cannot assert on a live one's output, and the
blob store is the only other one, because a unit test should not need a bucket.

Everything else is real: the session, the constraints, the validator, the renderer (which
genuinely compiles a PDF), the state transition and the event.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import storage
from db.models import Document, Event, Evidence, Job, Match, Profile, User
from schemas.enums import DocumentType, EvidenceOrigin, MatchStatus
from schemas.tailoring import (
    CoverLetterDraft,
    CoverLetterParagraph,
    TailoredBullet,
    TailoredResume,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from workers import llm
from workers.tailoring import tailor

EVALS = Path(__file__).resolve().parents[2] / "evals" / "fabrication"
VAULT: dict[str, Any] = json.loads((EVALS / "vaults.json").read_text())["vaults"][
    "senior_backend.pdf"
]
PARSED: dict[str, Any] = json.loads((EVALS.parent / "golden" / "pairs.json").read_text())[
    "profiles"
]["senior_backend.pdf"]["parsed_json"]

MODEL = "test/strong-model"


@pytest.fixture
def seeded(session: Session) -> Match:
    """A profile with M3's real vault, a job, and one `discovered` match."""
    user = User(email=f"{uuid.uuid4().hex}@example.com", auth_id=uuid.uuid4().hex)
    session.add(user)
    session.flush()

    profile = Profile(user_id=user.id, master_resume="unused here", parsed_json=PARSED)
    session.add(profile)
    session.flush()

    session.add_all(
        Evidence(
            profile_id=profile.id,
            kind=claim["kind"],
            text=claim["text"],
            source=claim["source"],
            origin=EvidenceOrigin.PARSED.value,
        )
        for claim in VAULT["claims"]
    )

    job = Job(
        source="greenhouse",
        external_id=f"acme:{uuid.uuid4().hex}",
        title="Senior Platform Engineer",
        company="Northwind Logistics",
        description="We need someone who has run Kubernetes migrations and cut latency.",
        url="https://boards.greenhouse.io/northwind/jobs/1",
        raw_json={},
    )
    session.add(job)
    session.flush()

    match = Match(user_id=user.id, job_id=job.id, score=71, status=MatchStatus.DISCOVERED.value)
    session.add(match)
    session.flush()
    return match


def _claim(handle: str) -> str:
    """The stored text for a handle, in the order `select.load` mints handles."""
    return VAULT["claims"][int(handle[1:]) - 1]["text"]


def _model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bullets: list[tuple[str, str]] | None = None,
    skills: list[str] | None = None,
    paragraphs: list[tuple[list[str], str]] | None = None,
) -> list[str]:
    """Fake the two completions. Returns the list the models were asked for, in order."""
    asked: list[str] = []
    resume = TailoredResume(
        bullets=[
            TailoredBullet(evidence_id=handle, text=text)
            for handle, text in (bullets if bullets is not None else _grounded())
        ],
        skills=skills if skills is not None else ["Python", "Kubernetes"],
    )
    letter = CoverLetterDraft(
        paragraphs=[
            CoverLetterParagraph(evidence_ids=cites, text=text)
            for cites, text in (
                paragraphs
                if paragraphs is not None
                else [
                    (
                        ["E13"],
                        f"At Halcyon Freight Systems I {_claim('E13')[0].lower()}"
                        + _claim("E13")[1:],
                    )
                ]
            )
        ]
    )

    def fake(schema: type, **kwargs: Any) -> Any:
        asked.append(schema.__name__)
        usage = kwargs.get("usage")
        if usage is not None:
            usage.append(llm.Usage(prompt_tokens=1200, completion_tokens=300))
        return resume if schema is TailoredResume else letter

    monkeypatch.setattr(llm, "complete_json", fake)
    return asked


def _grounded() -> list[tuple[str, str]]:
    """Six verbatim bullets — the honest case, and enough to clear the default floor."""
    return [(claim["id"], claim["text"]) for claim in VAULT["claims"] if claim["kind"] == "bullet"]


def _stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Capture what would have gone to the bucket."""
    written: dict[str, bytes] = {}

    def fake_put(key: str, data: bytes, content_type: str) -> str:
        assert content_type == "application/pdf"
        written[key] = data
        return key

    monkeypatch.setattr(storage, "put", fake_put)
    monkeypatch.setattr(tailor.storage, "put", fake_put)
    return written


def _run(session: Session, match: Match, **kwargs: Any) -> Any:
    return tailor.tailor_match(
        session,
        str(match.id),
        model=MODEL,
        strip_ceiling=kwargs.pop("strip_ceiling", 0.30),
        min_bullets=kwargs.pop("min_bullets", 3),
    )


def _status(session: Session, match_id: uuid.UUID) -> str:
    row = session.get(Match, match_id)
    assert row is not None
    return row.status


def _latest_event(session: Session, kind: str) -> Event | None:
    return session.scalars(
        select(Event).where(Event.type == kind).order_by(Event.created_at.desc())
    ).first()


# ------------------------------------------------------------------- the happy path


def test_a_discovered_match_becomes_two_documents_and_a_tailored_status(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _model(monkeypatch)
    written = _stored(monkeypatch)

    result = _run(session, seeded)

    assert result is not None and not result.blocked
    assert asked == ["TailoredResume", "CoverLetterDraft"], "two calls, never one"

    documents = list(session.scalars(select(Document).where(Document.match_id == seeded.id)))
    assert {document.type for document in documents} == {
        DocumentType.RESUME.value,
        DocumentType.COVER_LETTER.value,
    }
    assert all(document.storage_url in written for document in documents)
    assert all(written[document.storage_url].startswith(b"%PDF-") for document in documents)

    assert _status(session, seeded.id) == MatchStatus.TAILORED.value


def test_the_event_carries_the_counts_and_the_token_spend(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.2 wants per-run token spend, and §3.7 wants volume alerts. Both live here."""
    _model(monkeypatch)
    _stored(monkeypatch)

    _run(session, seeded)

    event = _latest_event(session, "tailor.generated")
    assert event is not None
    assert event.payload_json["prompt_tokens"] == 2400, "both calls counted, not one"
    assert event.payload_json["bullets_kept"] > 0
    assert event.payload_json["model"] == MODEL
    assert len(event.payload_json["documents"]) == 2


# ------------------------------------------------------------------------ the blocks


def test_a_fabricated_skill_blocks_and_writes_nothing(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clause that matters. Part 13 rule 2: nothing reaches a PDF, so nothing reaches
    the bucket, and the match stays where M4 left it."""
    _model(monkeypatch, skills=["Python", "Rust"])
    written = _stored(monkeypatch)

    result = _run(session, seeded)

    assert result is not None and result.blocked
    assert "Rust" in (result.reason or "")
    assert not written, "a blocked document was rendered and stored anyway"
    assert not list(session.scalars(select(Document).where(Document.match_id == seeded.id)))
    assert _status(session, seeded.id) == MatchStatus.DISCOVERED.value

    event = _latest_event(session, "tailor.blocked")
    assert event is not None
    assert event.payload_json["fabricated_skills"] == 1


def test_an_ungrounded_bullet_is_stripped_with_its_reason_recorded(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Below the ceiling, a strip is not a block — the document ships shorter and true."""
    bullets = _grounded()
    bullets.append(("E13", "Cut p95 checkout latency by 76% using Azure Cache for Redis"))
    _model(monkeypatch, bullets=bullets)
    _stored(monkeypatch)

    result = _run(session, seeded)

    assert result is not None and not result.blocked
    assert result.bullets_stripped == 1
    assert result.bullets_kept == len(bullets) - 1
    assert _status(session, seeded.id) == MatchStatus.TAILORED.value


def test_a_cover_letter_that_adds_a_claim_costs_the_letter_and_not_the_resume(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two documents are judged separately.

    M5's third live gate discarded 7 validated résumés because a letter paragraph used a
    word like `offer`. Nothing false shipped in any of them — the validator worked — so
    throwing away the good document punished the wrong thing. The letter is simply not
    written, and the match still moves on with its résumé.
    """
    _model(
        monkeypatch,
        paragraphs=[(["E13"], "I hold an AWS Solutions Architect certification.")],
    )
    written = _stored(monkeypatch)

    result = _run(session, seeded)

    assert result is not None
    assert result.letter_blocked and not result.blocked
    assert _status(session, seeded.id) == MatchStatus.TAILORED.value

    documents = list(session.scalars(select(Document).where(Document.match_id == seeded.id)))
    assert [document.type for document in documents] == [DocumentType.RESUME.value]
    assert len(written) == 1, "a letter that did not validate was rendered anyway"

    event = _latest_event(session, "tailor.generated")
    assert event is not None
    assert event.payload_json["letter_blocked"] is True
    assert event.payload_json["letter_bytes"] == 0


def test_the_mirror_link_lands_on_the_document_row(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stage half of the gate's mirror clause.

    `make verify-live-drive` proves the upload against the real Drive; this proves the
    wiring — that what the mirror returns reaches `documents.gdrive_url`, which is the
    column M6 reads to put a link in a message.
    """
    _model(monkeypatch)
    _stored(monkeypatch)
    monkeypatch.setattr(tailor.drive, "is_configured", lambda: True)
    monkeypatch.setattr(
        tailor.drive,
        "upload",
        lambda **kwargs: f"https://drive.google.com/file/d/{kwargs['name']}/view",
    )

    _run(session, seeded)

    documents = list(session.scalars(select(Document).where(Document.match_id == seeded.id)))
    assert documents
    for document in documents:
        assert document.gdrive_url is not None
        assert document.type in document.gdrive_url, "the wrong document's link was stored"


def test_a_mirror_that_fails_costs_the_link_and_not_the_document(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 holds the durable copy and the model call has already been paid for, so a Drive
    outage must not discard a document. It is recorded rather than raised, because a
    mirror that has quietly stopped working raises no error and shows in no error rate."""
    _model(monkeypatch)
    _stored(monkeypatch)
    monkeypatch.setattr(tailor.drive, "is_configured", lambda: True)

    def explode(**_: object) -> str:
        raise tailor.drive.DriveError("drive refused the upload: HTTP 503")

    monkeypatch.setattr(tailor.drive, "upload", explode)

    result = _run(session, seeded)

    assert result is not None and not result.blocked
    documents = list(session.scalars(select(Document).where(Document.match_id == seeded.id)))
    assert documents and all(document.gdrive_url is None for document in documents)
    assert _status(session, seeded.id) == MatchStatus.TAILORED.value

    event = _latest_event(session, "tailor.mirror_failed")
    assert event is not None
    assert "503" in event.payload_json["error"]


# ------------------------------------------------------------------- idempotency (§3.4)


def test_a_second_run_over_the_same_match_does_nothing(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state machine is the lock. No fingerprint column, no check-then-act: the match
    is no longer `discovered`, so there is nothing to select."""
    _model(monkeypatch)
    _stored(monkeypatch)
    first = _run(session, seeded)

    second = _run(session, seeded)

    assert first is not None and second is None
    assert len(list(session.scalars(select(Document).where(Document.match_id == seeded.id)))) == 2


def test_a_match_that_is_not_discovered_is_never_tailored(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M6's approval and M4's rescore both write this column. Tailoring acts on exactly
    one state, so neither can be undone by a scheduled run."""
    seeded.status = MatchStatus.APPROVED.value
    session.flush()
    _model(monkeypatch)
    _stored(monkeypatch)

    assert _run(session, seeded) is None
    assert _status(session, seeded.id) == MatchStatus.APPROVED.value


def test_a_profile_that_never_parsed_is_not_tailorable(
    session: Session, seeded: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guard `match_all` uses one stage earlier: an empty `parsed_json` means the
    parse failed, and tailoring it would ground a document on nothing."""
    profile = session.scalar(select(Profile).where(Profile.user_id == seeded.user_id))
    assert profile is not None
    profile.parsed_json = {}
    session.flush()
    _model(monkeypatch)

    assert _run(session, seeded) is None
