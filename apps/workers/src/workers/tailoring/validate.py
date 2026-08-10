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

# **A number is a number however it is spelled.** The résumé path never needed this — it
# checks every token, so `nine` fails there as ordinary unsourced content. The letter path
# checks facts rather than vocabulary, and without this a model writes "nine years" where
# it cannot write "9 years", which is the same claim wearing a different hat. Case CL-03
# is exactly that sentence and is what caught the omission.
# An evidence handle the model wrote into the prose instead of leaving in `evidence_ids`.
# Optionally wrapped, because it arrives as `(E11)`, `[E11, E12]` or bare.
_HANDLE_IN_PROSE = re.compile(r"[(\[]\s*E\d+(?:\s*,\s*E\d+)*\s*[)\]]|\bE\d+\b")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

_NUMBERS_SPELLED = """
zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy
eighty ninety hundred thousand million billion dozen
"""
_NUMBER_WORDS = frozenset(_NUMBERS_SPELLED.split())


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


def resume(
    *,
    bullets: list[TailoredBullet],
    skills: list[str],
    vault: Vault,
    company: str = "",
    title: str = "",
) -> ResumeReport:
    """Check every generated bullet against the claim it says it came from.

    `company` and `title` are the *posting's*, and they are the only two strings from the
    posting a bullet may use — the same two the letter path has always allowed, for the
    same reason: naming the employer being applied to is not a claim about the candidate.
    A bullet reading "the platform work Stripe is hiring for" is honest; the facts in it
    are still checked against the cited claim. Nothing else from the posting is admitted,
    because "take the posting's vocabulary" and class F2 — vault says AWS, posting says
    Azure, bullet says Azure — are the same code change.

    They default to empty so the eval harness and the offline cases, which judge a bullet
    against a vault and no posting, keep their current meaning exactly.
    """
    own = squash(
        " ".join(vault.companies) + " " + " ".join(vault.titles) + " " + company + " " + title
    )

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
    """**Facts, not vocabulary** — the one place this file's rule differs from the résumé's.

    A bullet *is* a rewrite of one claim, so demanding its words come from that claim is a
    fair proxy for "same facts, different phrasing". A letter paragraph is an argument
    built *from* claims, and the identical demand is not a fabrication rule at all — it is
    an instruction not to write. Ten live pairs on 2026-08-10 rejected `'backend'`,
    `'infrastructure'`, `'team'`, `'migrations'`, `'accountabilities'`, `'experience'`.
    None is a fabrication. They are what prose is made of, and no achievable word list
    fixes that, because the next paragraph needs different ones.

    So a paragraph is checked for the things that can be *false*:

    - **numbers**, against the cited claim alone and nothing wider. Unchanged, and this is
      the rule that earns its keep — it caught `the number 13` and `the number 17` on
      `gpt-5`, and three of five live pairs here, every one a years-of-experience total
      the résumé never stated.
    - **capitalised tokens**, against the vault. Technologies, employers, products and
      tools are capitalised in English, so this is what keeps class F2 dead: the vault
      says AWS, the posting says Azure, and `Azure` is still refused.
    - everything else is lowercase prose and asserts nothing on its own.

    **What this gives up, stated rather than discovered.** Class F3 weakens *for letters*:
    "led a cross-functional team" now passes where it did not, though the `50+` in it
    still does not. A lowercase technology — `pytest`, `npm` — slips. Both are real, and
    against them: the résumé keeps the strict rule and is the document an employer parses,
    a letter is read by a human before M6 sends anything, and §2 of BAR.md reports the
    letter rather than gating it. A stage that produced nothing on 10 of 10 honest pairs
    was not protecting anybody.

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

        # Cleaned once, here, so the text that is judged is the text that is rendered.
        paragraph = paragraph.model_copy(update={"text": strip_handles(paragraph.text)})

        cited = [vault.claims[name] for name in paragraph.evidence_ids]
        cited_text = " ".join(claim.text for claim in cited)
        haystack = squash(cited_text) + allowed_context + _alias_context(cited_text)
        # Digits and spelled-out numbers together, from the cited claims alone — never
        # from the wider vault, because the most convincing metric fabrication is a real
        # number borrowed from a different role.
        numbers = {run for claim in cited for run in _DIGITS.findall(claim.text)}
        numbers |= {
            squashed
            for token in _TOKENS.findall(cited_text)
            if (squashed := squash(token)) in _NUMBER_WORDS
        }

        fault = _unfounded(paragraph.text, haystack, numbers)
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


def strip_handles(text: str) -> str:
    """Remove evidence handles the model wrote into the prose.

    **These are our labels, not the candidate's claims, and they were failing the letter
    on the number rule.** `select.py` mints `E1`…`E20`; the model is told to put them in
    `evidence_ids`, and it also writes them into the sentence — `(E11)`, `[E11, E12]`, or
    bare. `_DIGITS` then reads `E11` as the number 11, finds no 11 in the cited claim, and
    rejects a paragraph that invented nothing.

    Measured 2026-08-10: six consecutive live letters rejected on "the number 11", "the
    number 12", "the number 13" against a vault whose letter claims were exactly E11, E12,
    E13, E15, E16, E18. **It also puts the earlier catches in doubt** — `the number 13` and
    `the number 17` on `gpt-5`, recorded in BAR.md §8 as F4 saves, came from vaults with at
    least that many claims and were most likely this.

    Stripped rather than merely ignored, because the cleaned text is what gets rendered: a
    letter that passed with `(E11)` in it would print the handle onto the PDF.
    """
    cleaned = _HANDLE_IN_PROSE.sub("", text)
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    return _MULTI_SPACE.sub(" ", cleaned).strip()


def _unfounded(text: str, haystack: str, numbers: set[str]) -> str | None:
    """Rule 2 in full, and rule 3 narrowed to the tokens that can carry a fact.

    The letter's half of the guarantee. See `cover_letter` for why it differs from
    `_untraceable`, which is unchanged and still governs every résumé bullet.

    Capitalisation is checked at every position, including the start of a sentence: a
    model that opens with "Kubernetes underpinned the platform" is making the same claim
    as one that says it mid-sentence, and skipping sentence-initial tokens would leave
    exactly that hole. The cost is that a sentence may not *open* with a capitalised
    common noun unless `words.ALLOWED` carries it — which is a much smaller demand on that
    list than the old rule made, because it now only has to cover sentence openers rather
    than every word of every paragraph.
    """
    for run in _DIGITS.findall(text):
        if run not in numbers:
            return f"the number {run} is not in the cited evidence"

    for token in _TOKENS.findall(text):
        word = squash(token)
        if not word or word.isdigit():
            continue
        if word in _NUMBER_WORDS and word not in numbers:
            # Before the ALLOWED check, deliberately: a number word must not become
            # legal by being grammar-shaped.
            return f"the number {token!r} is not in the cited evidence"
        if word in ALLOWED:
            continue
        if not token[0].isupper():
            # Lowercase prose. It joins the facts together and is not one of them.
            continue
        if word in haystack:
            continue
        # `APIs` where the claim wrote `API`, `Services` where it wrote `Service`.
        # Pluralising a term the evidence already names adds no claim, and refusing it
        # rejects a paragraph for grammar. Only this direction: dropping a final `s`
        # cannot turn one technology into another.
        if word.endswith("s") and word[:-1] in haystack:
            continue
        return f"{token!r} does not appear in the cited evidence"
    return None


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
