"""The guardrail. §3.3, enforcement point 2 — the one that is not a prompt.

Nothing generated reaches a PDF without passing through here (Part 13 rule 2). The
prompt is instructed to ground everything in the vault; this file assumes it did not.

**Everything is diffed against the claim the bullet cites, never against the vault as a
whole.** That is the design's load-bearing choice and it is what catches the three
fabrication classes a whole-vault check waves straight through: a number that is real in
another role, a skill that is real in another job, a technology that is real but postdates
the role it is attached to. Each is a *true* fact about the candidate placed somewhere it
was never true, and the vault contains every one of them.

Five rules, in order, cheapest first:

    1. provenance   the cited handle exists, and names a bullet claim
    2. numbers      every digit-run in the text is in the cited claim
    3. content      every token is in the cited claim, or in words.ALLOWED, or is
                    the candidate's own employer or job title
    4. skills       exact membership in stored skill claims — no rewriting at all
    5. structure    not enforced here, because it cannot be violated: names, employers,
                    titles, dates and education are assembled by code from parsed_json

Comparison is `workers.text.squash`, the same transform M3 used when it decided the claim
was true. If the two ends of that chain drift apart the guarantee breaks in the middle
without either end failing — `tests/unit/test_fabrication_guard.py` asserts they agree.

This module reports; it does not decide. `verdict()` turns a report into a block using
ceilings the caller owns, the same split M4 uses between `score()` and the threshold.
"""

import re
from dataclasses import dataclass
from typing import Any

from schemas.enums import EvidenceKind
from schemas.tailoring import CoverLetterParagraph, TailoredBullet

from workers.tailoring import aliases
from workers.tailoring.words import ALLOWED
from workers.text import split_skill_line, squash

_DIGITS = re.compile(r"\d+")
_TOKENS = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Claim:
    """One vault row, as the validator needs it."""

    id: str
    kind: str
    text: str
    source: str


@dataclass(frozen=True)
class Vault:
    """Everything a generated document is allowed to be built from.

    `companies` and `titles` are not evidence rows and are here anyway: a bullet may name
    the employer it happened at without that being a new claim, and refusing it would
    strip half of any cover letter.
    """

    claims: dict[str, Claim]
    companies: tuple[str, ...]
    titles: tuple[str, ...]

    def skill_texts(self) -> dict[str, str]:
        """Squashed skill -> the stored spelling, which is what gets rendered.

        **A stored skill claim is sometimes a whole résumé line.** M3 usually splits
        `Languages: Python, Go, SQL` into one claim per keyword and sometimes does not —
        it is a sampling property of the parse, not of the résumé, and the same fixture
        splits on one run and not the next. M5's first live run met the unsplit shape and
        called every one of the candidate's real skills a fabrication, which blocked the
        document outright.

        So a group line is also read as its members, via `text.split_skill_line` — the
        same splitter M3 now applies at write time, shared so the two ends cannot drift.
        This stays here as well as there: vaults stored before M3 split are still on disk,
        and a validator that trusted the parser to have done it would call the candidate's
        own skills fabrications on every one of them.
        """
        found: dict[str, str] = {}
        for claim in self.claims.values():
            if claim.kind != EvidenceKind.SKILL.value:
                continue
            found.setdefault(squash(claim.text), claim.text)
            for part in split_skill_line(claim.text):
                found.setdefault(squash(part), part)
                # `Postgres` in the vault also answers for `PostgreSQL`, which is the
                # string the posting and the ATS use. One direction only — see
                # `aliases.py`, and case S-06, which is the reverse and still blocks.
                fuller = aliases.expand(part)
                if fuller:
                    found.setdefault(squash(fuller), fuller)
        return found


@dataclass(frozen=True)
class Stripped:
    """A bullet that did not survive, and the sentence explaining why.

    The reason is not decoration. §3.7 says loud failure beats silent success, and a
    stripped-bullet count with no reasons is a number nobody can act on.
    """

    evidence_id: str
    text: str
    reason: str


@dataclass(frozen=True)
class ResumeReport:
    kept: list[TailoredBullet]
    stripped: list[Stripped]
    skills: list[str]
    fabricated_skills: list[str]

    @property
    def strip_rate(self) -> float:
        total = len(self.kept) + len(self.stripped)
        return len(self.stripped) / total if total else 0.0


@dataclass(frozen=True)
class LetterReport:
    paragraphs: list[CoverLetterParagraph]
    rejected: list[Stripped]

    @property
    def blocked(self) -> bool:
        """Any bad paragraph fails the whole letter.

        Deliberately unlike the résumé, where a stripped bullet leaves a shorter but true
        document. A letter is continuous prose the candidate signs as their own words; a
        paragraph removed from the middle of it leaves an argument with a hole, and the
        remaining paragraphs still sit under a sentence that referred to the missing one.
        """
        return bool(self.rejected)


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    reason: str | None


def vault_from(blob: dict[str, Any]) -> Vault:
    """Build a `Vault` from the plain shape `select.py` and the eval fixtures both emit."""
    return Vault(
        claims={
            row["id"]: Claim(id=row["id"], kind=row["kind"], text=row["text"], source=row["source"])
            for row in blob["claims"]
        },
        companies=tuple(blob["companies"]),
        titles=tuple(blob["titles"]),
    )


def resume(*, bullets: list[TailoredBullet], skills: list[str], vault: Vault) -> ResumeReport:
    """Check every generated bullet against the claim it says it came from."""
    own = squash(" ".join(vault.companies) + " " + " ".join(vault.titles))

    kept: list[TailoredBullet] = []
    stripped: list[Stripped] = []
    for bullet in bullets:
        reason = _fault(bullet, vault, own)
        if reason is None:
            kept.append(bullet)
        else:
            stripped.append(Stripped(bullet.evidence_id, bullet.text, reason))

    stored = vault.skill_texts()
    selected = [skill for skill in skills if squash(skill) in stored]
    fabricated = [skill for skill in skills if squash(skill) not in stored]

    return ResumeReport(kept=kept, stripped=stripped, skills=selected, fabricated_skills=fabricated)


def cover_letter(
    *,
    paragraphs: list[CoverLetterParagraph],
    vault: Vault,
    company: str,
    title: str,
) -> LetterReport:
    """Same rules, over prose, with the posting's own name and title added.

    The employer being written to is not a claim about the candidate, so naming it is
    allowed. Nothing else from the posting is: the job description is the *temptation*
    here, never a permission source. M4 already measured this exact model emitting the
    candidate's own résumé sentence as a posting requirement once the two shared a
    prompt, and this is the same collision seen from the other side.
    """
    allowed_context = squash(
        " ".join(vault.companies) + " " + " ".join(vault.titles) + " " + company + " " + title
    )

    good: list[CoverLetterParagraph] = []
    rejected: list[Stripped] = []
    for paragraph in paragraphs:
        missing = [name for name in paragraph.evidence_ids if name not in vault.claims]
        if missing:
            rejected.append(
                Stripped(
                    ",".join(paragraph.evidence_ids),
                    paragraph.text,
                    f"cites {', '.join(missing)}, which is not in the vault",
                )
            )
            continue

        cited = [vault.claims[name] for name in paragraph.evidence_ids]
        cited_text = " ".join(claim.text for claim in cited)
        haystack = squash(cited_text) + allowed_context + _alias_context(cited_text)
        digits = {run for claim in cited for run in _DIGITS.findall(claim.text)}

        fault = _untraceable(paragraph.text, haystack, digits)
        if fault is None:
            good.append(paragraph)
        else:
            rejected.append(Stripped(",".join(paragraph.evidence_ids), paragraph.text, fault))

    return LetterReport(paragraphs=good, rejected=rejected)


def verdict(
    report: ResumeReport, *, strip_ceiling: float, min_bullets: int, available_bullets: int
) -> Verdict:
    """Ship this résumé, or block it? The caller owns both ceilings.

    Stripping is the normal case and blocking is the loud one. Three things earn it:

    A **fabricated skill** blocks outright, at any count. §3.3 names inventing a skill as
    the adversarial case, the skills block is the densest keyword surface on the page, and
    a model reaching for one the candidate does not have is not making a rounding error.

    A **strip rate over the ceiling** blocks because the model has stopped grounding
    rather than slipped once, and shipping the surviving third of a résumé is worse than
    shipping nothing — it looks finished.

    **Too few bullets** blocks because a two-bullet résumé is not a document a user would
    send, however true it is — but the floor is capped by what the vault can actually
    supply. A résumé with four stored bullets cannot produce six, so an uncapped floor of
    six is not a quality bar for that profile, it is a guaranteed block: the stage would
    refuse every document it was ever asked for and the reason would look like model
    quality. M4 recorded the same shape one milestone earlier — *a label the code cannot
    reach is not a bar, it is a guaranteed false positive.*

    M5's first live gate blocked 19 of 20 honest pairs on exactly this, with retention at
    0.98: the validator was barely stripping anything, and the floor was unsatisfiable for
    one fixture and only satisfiable by using every single bullet for the other — which
    the résumé prompt explicitly tells the model not to do.
    """
    if report.fabricated_skills:
        return Verdict(True, f"skills not in the vault: {', '.join(report.fabricated_skills)}")
    if report.strip_rate > strip_ceiling:
        return Verdict(
            True,
            f"{report.strip_rate:.0%} of bullets were untraceable, ceiling {strip_ceiling:.0%}",
        )
    floor = min(min_bullets, available_bullets)
    if len(report.kept) < floor:
        return Verdict(
            True,
            f"only {len(report.kept)} traceable bullets, need {floor}"
            + (
                f" (capped from {min_bullets} by {available_bullets} in the vault)"
                if floor != min_bullets
                else ""
            ),
        )
    return Verdict(False, None)


def _fault(bullet: TailoredBullet, vault: Vault, own: str) -> str | None:
    """Rules 1-3 for one bullet. `None` means it survives."""
    claim = vault.claims.get(bullet.evidence_id)
    if claim is None:
        # A mistyped or invented handle. The text may well be true; provenance that
        # cannot be named is not provenance, and the alternative — guessing which claim
        # was meant — is how a bullet lands on a neighbouring row.
        return f"cites {bullet.evidence_id}, which is not in the vault"
    if claim.kind != EvidenceKind.BULLET.value:
        # A skill or a credential is a fact, not a piece of work. "Wrote Python across the
        # billing services" citing the skill `Python` is a work claim wearing a skill's
        # citation, and the skill cannot support the part that matters.
        return f"cites {claim.id}, a {claim.kind} claim; a bullet must cite a bullet"

    haystack = squash(claim.text) + own + _alias_context(claim.text)
    return _untraceable(bullet.text, haystack, set(_DIGITS.findall(claim.text)))


def _alias_context(text: str) -> str:
    """The fuller vendor spellings the words of this claim license, squashed.

    Token by token, and only from the claim being cited — so a bullet may write
    `PostgreSQL` where the claim wrote `Postgres`, and may not write it where the claim
    named neither. Expansion only; `aliases.py` carries the argument and case S-06.
    """
    return "".join(
        squash(fuller)
        for token in _TOKENS.findall(text)
        if (fuller := aliases.expand(token)) is not None
    )


def _untraceable(text: str, haystack: str, digits: set[str]) -> str | None:
    """Rules 2 and 3, shared by bullets and paragraphs."""
    for run in _DIGITS.findall(text):
        if run not in digits:
            # Numbers are checked against the cited claim alone and never against the
            # wider vault, because the most convincing metric fabrication is a real
            # number from a different role.
            return f"the number {run} is not in the cited evidence"

    for token in _TOKENS.findall(text):
        word = squash(token)
        if not word or word.isdigit() or word in ALLOWED:
            continue
        if word not in haystack:
            return f"{token!r} does not appear in the cited evidence"
    return None
