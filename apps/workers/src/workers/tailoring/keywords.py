"""How much of this posting's own vocabulary the tailored résumé carries — deterministic.

An ATS scores a résumé by matching strings out of the posting against strings in the
document. That is a different question from M4's score, which is coverage of the posting's
stated *requirements* against the candidate's résumé and is computed before any tailoring
happens. This is the question tailoring can actually move, and until now nothing reported
it, so the two fixes that exist to raise it — splitting group skill lines, and letting a
vault spelling reach its fuller vendor form — were invisible.

**The denominator is deliberately narrow: skills the candidate already holds AND the
posting names.** Not every noun in the posting, which would make the number a measure of
how much the candidate is missing, and not every skill in the vault, which would punish a
résumé for omitting things this employer never asked about. What is left is exactly the
set a human would call "the keywords I should be surfacing for this job", and every member
of it is already in the vault — so a low number is a selection to argue with, never a
suggestion to invent something.

No model call. It has to be deterministic or it is one more thing to trust.
"""

import re

from workers.tailoring import aliases
from workers.text import split_skill_line, squash

_TOKENS = re.compile(r"[A-Za-z0-9+#.]+")


def _bag(text: str) -> str:
    """Text as squashed tokens with spaces kept between them, for whole-word matching.

    `squash` alone would join the whole string into one blob, and `Go` is a substring of
    `going`, `golang` and `ongoing` — which would inflate this number on almost every
    posting. The validator can use the blob because a false *match* there only ever admits
    a bullet the candidate could have written; here a false match is a wrong number on a
    screen, so the tokens keep their edges.
    """
    return " " + " ".join(squash(token) for token in _TOKENS.findall(text) if squash(token)) + " "


def _holds(bag: str, phrase: str) -> bool:
    """Is this skill present in that bag as whole tokens? Handles `Google Cloud Platform`."""
    needle = " ".join(squash(token) for token in _TOKENS.findall(phrase) if squash(token))
    return bool(needle) and f" {needle} " in bag


def _forms(skill: str) -> list[str]:
    """The spellings this vault skill may legitimately appear as: its own, plus the fuller
    vendor form it licenses. Same direction and same table as the validator uses."""
    fuller = aliases.expand(skill)
    return [skill, fuller] if fuller else [skill]


def vault_skills(claims: list[str]) -> list[str]:
    """Stored skill claim texts -> the individual skills inside them, order preserved.

    A stored claim is sometimes a whole `Languages: Python, Go` line, so the members are
    split out the same way M3 and the validator split them. The group LABEL is dropped —
    `Languages` and `Infrastructure` are headings, and counting them as keywords would
    inflate both sides of the ratio with words no ATS is looking for.
    """
    found: dict[str, str] = {}
    for claim in claims:
        parts = split_skill_line(claim)
        # A claim with no delimiter is already one skill; a split one contributes its
        # members but not its first part, which is the heading.
        for part in parts[1:] if len(parts) > 1 else parts:
            found.setdefault(squash(part), part)
    return list(found.values())


def coverage(*, claims: list[str], description: str, rendered: str) -> dict[str, object]:
    """`{matched, total, missing}` for one tailored résumé.

    `rendered` is every string that reaches the document — the selected skills and the
    surviving bullets — because a skill named in a bullet counts to an ATS exactly as much
    as one in the skills section.
    """
    posting = _bag(description)
    document = _bag(rendered)

    matched: list[str] = []
    missing: list[str] = []
    for skill in vault_skills(claims):
        forms = _forms(skill)
        if not any(_holds(posting, form) for form in forms):
            continue
        if any(_holds(document, form) for form in forms):
            matched.append(skill)
        else:
            missing.append(skill)

    return {
        "keywords_matched": len(matched),
        "keywords_total": len(matched) + len(missing),
        "keywords_missing": missing,
    }
