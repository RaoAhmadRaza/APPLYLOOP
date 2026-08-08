"""§3.3's guardrail, against `evals/fabrication/cases.json`.

**This file is permanent. Never deleted, never skipped, never marked xfail** — CLAUDE.md
§3.3 and Part 13 rule 3. If it appears in a diff being written, stop and ask.

It is the free half of the M5 gate: no model, no network, no money, runs in CI on every
push. The paid half puts adversarial *postings* in front of a real model and checks what
comes back; this half puts adversarial *output* in front of the validator and checks what
it does. Both are needed, and M3's lesson is why: a stubbed model tests the plumbing while
the judgement stays unmeasured — so the plumbing is tested here, exhaustively and for
free, and the money is spent only on the judgement.

Every seeded fabrication was written before `validate.py` existed (BAR.md §3 rule 1), so
none of them is a case reverse-engineered from the code's behaviour.

**Why this runs even while the case set is `proposed`.** BAR.md §6 says a model may author
a case and only a human may confirm one, and these were authored by a model. That rule
governs whether the cases may be *counted toward the bar*, which the live gate enforces.
It does not govern whether they run: an unconfirmed case that the validator fails is still
a finding, and a permanent test that skips itself is not permanent.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from schemas.tailoring import CoverLetterParagraph, TailoredBullet
from workers.profiles import vault as m3_vault
from workers.tailoring import validate

EVALS = Path(__file__).resolve().parents[2] / "evals" / "fabrication"
CASES: dict[str, Any] = json.loads((EVALS / "cases.json").read_text())
VAULTS: dict[str, Any] = json.loads((EVALS / "vaults.json").read_text())["vaults"]

OFFLINE = CASES["offline"]
FABRICATIONS = OFFLINE["fabrications"]
SKILL_CASES = OFFLINE["fabricated_skills"]
FAITHFUL = OFFLINE["faithful"]
LETTERS = OFFLINE["cover_letter"]

# BAR.md §2. Read from the bar rather than re-derived, so changing it means editing the
# file whose git history is the evidence.
RETENTION_FLOOR = 0.70


def _vault(profile: str) -> validate.Vault:
    return validate.vault_from(VAULTS[profile])


def _one(case: dict[str, Any]) -> validate.ResumeReport:
    """Run a single bullet case through the résumé validator."""
    return validate.resume(
        bullets=[TailoredBullet(evidence_id=case["cites"], text=case["text"])],
        skills=[],
        vault=_vault(case["profile"]),
    )


# ------------------------------------------------------- the gate clause, §3.3's promise


def test_fabrication_guard() -> None:
    """**The permanent adversarial test.** Not one seeded fabrication reaches a document.

    BAR.md §2 sets this at 1.00 and says why it is not a proportion to optimise: these are
    the cases where the lie is known because a human wrote it down, so anything less than
    all of them is a validator that does not work.
    """
    escaped = []
    for case in FABRICATIONS:
        report = _one(case)
        if report.kept:
            escaped.append((case["id"], case["class"], case["text"]))

    blocking = [case for case in SKILL_CASES if case["expect"] == "blocks"]
    for case in blocking:
        report = validate.resume(bullets=[], skills=case["skills"], vault=_vault(case["profile"]))
        if not report.fabricated_skills:
            escaped.append((case["id"], "skill", ", ".join(case["skills"])))

    by_class: dict[str, int] = {}
    for case in FABRICATIONS:
        by_class[case["class"]] = by_class.get(case["class"], 0) + 1
    # Counted from the data, never from a literal: cases are only ever added (BAR.md §3
    # rule 3), so a hardcoded total silently stops counting the newest ones.
    total = len(FABRICATIONS) + len(blocking)
    print(f"\n  seeded fabrications caught: {total - len(escaped)}/{total}  by class: {by_class}")

    assert not escaped, (
        "a seeded fabrication reached a document — BAR.md §2's catch rate is 1.00 and "
        "this is not it:\n" + "\n".join(f"  {i} [{c}] {t}" for i, c, t in escaped)
    )


@pytest.mark.parametrize("case", FABRICATIONS, ids=lambda case: str(case["id"]))
def test_each_seeded_fabrication_is_stripped(case: dict[str, Any]) -> None:
    """Same assertion, one case at a time, so a failure names the class it belongs to
    instead of a count. The `lie` field is printed because a bare id is not reviewable."""
    report = _one(case)

    assert not report.kept, f"{case['id']} [{case['class']}] survived — the lie: {case['lie']}"
    assert report.stripped[0].reason, "a strip with no stated reason cannot be diagnosed"


@pytest.mark.parametrize("case", SKILL_CASES, ids=lambda case: str(case["id"]))
def test_a_skill_outside_the_vault_is_never_selected(case: dict[str, Any]) -> None:
    """§3.3 names inventing a *skill* as the adversarial case this test must catch, which
    is why the skills field has no rewriting allowance at all: exact membership."""
    report = validate.resume(bullets=[], skills=case["skills"], vault=_vault(case["profile"]))

    if case["expect"] == "blocks":
        assert report.fabricated_skills, f"{case['id']}: {case['lie']}"
    else:
        assert not report.fabricated_skills, f"{case['id']}: {case['why']}"
        assert report.skills == case["skills"]


def test_a_fabricated_skill_blocks_the_whole_document() -> None:
    """Stripping is the normal case; this is the loud one.

    A model reaching for a skill the candidate does not have is not a rounding error, and
    the skills block is the densest keyword surface on the page. The strip alone would be
    silent — §3.7 — so the document does not ship at all.
    """
    report = validate.resume(
        bullets=[
            TailoredBullet(
                evidence_id="E13", text=VAULTS["senior_backend.pdf"]["claims"][12]["text"]
            )
        ],
        skills=["Python", "Rust"],
        vault=_vault("senior_backend.pdf"),
    )

    call = validate.verdict(report, strip_ceiling=0.30, min_bullets=1)

    assert call.blocked
    assert call.reason is not None and "Rust" in call.reason


def test_a_document_stripped_past_the_ceiling_is_blocked_rather_than_shipped_short() -> None:
    """Shipping the surviving third of a résumé is worse than shipping nothing: it looks
    finished. The ceiling is the caller's, the same split M4 uses for its threshold."""
    vault_ = _vault("senior_backend.pdf")
    good = VAULTS["senior_backend.pdf"]["claims"][12]["text"]
    report = validate.resume(
        bullets=[
            TailoredBullet(evidence_id="E13", text=good),
            TailoredBullet(evidence_id="E13", text="Held a Rust certification while doing it"),
            TailoredBullet(evidence_id="E13", text="Ran a team of 9 across three continents"),
        ],
        skills=[],
        vault=vault_,
    )

    assert report.strip_rate == pytest.approx(2 / 3)
    assert validate.verdict(report, strip_ceiling=0.30, min_bullets=1).blocked
    assert not validate.verdict(report, strip_ceiling=0.90, min_bullets=1).blocked


def test_too_few_surviving_bullets_is_not_a_document() -> None:
    """True and unsendable is still a failure. Without this the strip-rate ceiling passes
    a résumé whose model returned two bullets and grounded both."""
    report = validate.resume(
        bullets=[
            TailoredBullet(
                evidence_id="E13", text=VAULTS["senior_backend.pdf"]["claims"][12]["text"]
            )
        ],
        skills=[],
        vault=_vault("senior_backend.pdf"),
    )

    assert validate.verdict(report, strip_ceiling=0.30, min_bullets=6).blocked
    assert not validate.verdict(report, strip_ceiling=0.30, min_bullets=1).blocked


# ------------------------------------------------- the counterpart: it must keep things


@pytest.mark.parametrize("case", FAITHFUL, ids=lambda case: str(case["id"]))
def test_a_faithful_rewrite_survives(case: dict[str, Any]) -> None:
    """BAR.md §2's retention rows, one case at a time.

    Without these every assertion above is satisfiable by `return []`. This is the recall
    floor's counterpart and the bar file marks it as its single most important line.
    """
    report = validate.resume(
        bullets=[TailoredBullet(evidence_id=case["of"], text=case["text"])],
        skills=[],
        vault=_vault(case["profile"]),
    )

    assert report.kept, (
        f"{case['id']} was stripped and should not have been. Why it is faithful: "
        f"{case['why']}\n  reason given: "
        f"{report.stripped[0].reason if report.stripped else '(none)'}"
    )


def test_retention_clears_the_floor() -> None:
    """The aggregate, because a per-case pass says nothing about the rate.

    Verbatim copies are separated from rewrites: BAR.md sets 1.00 on the first and 0.70
    on the second, and averaging them would let a broken normaliser hide behind a
    generous rewrite allowance.
    """
    verbatim = [case for case in FAITHFUL if case["kind"] == "verbatim"]
    rewrites = [case for case in FAITHFUL if case["kind"] == "rewrite"]

    def kept(cases: list[dict[str, Any]]) -> int:
        return sum(
            1
            for case in cases
            if validate.resume(
                bullets=[TailoredBullet(evidence_id=case["of"], text=case["text"])],
                skills=[],
                vault=_vault(case["profile"]),
            ).kept
        )

    kept_verbatim, kept_rewrites = kept(verbatim), kept(rewrites)
    rate = kept_rewrites / len(rewrites)
    print(
        f"\n  retention  verbatim {kept_verbatim}/{len(verbatim)}   "
        f"rewrites {kept_rewrites}/{len(rewrites)} = {rate:.2f}   floor {RETENTION_FLOOR}"
    )

    assert kept_verbatim == len(verbatim), (
        "a character-for-character copy of a stored claim was stripped — the normaliser "
        "is broken, not the model"
    )
    assert rate >= RETENTION_FLOOR, (
        f"retention {rate:.2f} is below BAR.md §2's floor of {RETENTION_FLOOR}. A "
        "validator this strict is fabrication-free by refusing to answer."
    )


# --------------------------------------------------------------------- the cover letter


@pytest.mark.parametrize("case", LETTERS, ids=lambda case: str(case["id"]))
def test_a_cover_letter_paragraph_traces_to_cited_evidence(case: dict[str, Any]) -> None:
    """The gate's third clause. A letter is prose the candidate signs as their own words,
    so a paragraph that adds a claim fails the whole letter rather than being trimmed."""
    report = validate.cover_letter(
        paragraphs=[CoverLetterParagraph(evidence_ids=case["cites"], text=case["text"])],
        vault=_vault(case["profile"]),
        company=case["company"],
        title=case["title"],
    )

    if case["expect"] == "blocks":
        assert report.blocked, f"{case['id']}: {case['lie']}"
    else:
        assert not report.blocked, (
            f"{case['id']} was blocked and should not have been. {case['why']}\n"
            f"  reason: {report.rejected[0].reason if report.rejected else '(none)'}"
        )


# ------------------------------------------------------------------ the chain's joints


def test_both_ends_of_the_chain_use_the_same_normaliser() -> None:
    """§3.3 is enforced at two joins and they are the same comparison.

    If M3 stores a claim it considers contained in the résumé, and M5 refuses that same
    claim as a bullet, the guarantee has broken in the middle without either end failing.
    """
    for profile, blob in VAULTS.items():
        for claim in blob["claims"]:
            if claim["kind"] != "bullet":
                continue
            report = validate.resume(
                bullets=[TailoredBullet(evidence_id=claim["id"], text=claim["text"])],
                skills=[],
                vault=_vault(profile),
            )
            assert report.kept, (
                f"{profile} {claim['id']}: M3 stored this claim and M5 refuses it "
                "verbatim — the two ends of §3.3 disagree"
            )
            assert m3_vault.is_supported(claim["text"], claim["text"])


def test_the_case_set_records_who_authored_it() -> None:
    """BAR.md §6. A confirmed set names the human who confirmed it; a proposed one does
    not pretend to. The live gate is what refuses to count a proposed set — here the
    invariant is only that the file cannot claim confirmation anonymously."""
    meta = CASES["_meta"]

    assert meta["status"] in {"proposed", "confirmed"}
    if meta["status"] == "confirmed":
        assert str(meta["confirmed_by"]).startswith("human:"), (
            "a set confirmed by a model measures self-consistency — BAR.md §6"
        )
