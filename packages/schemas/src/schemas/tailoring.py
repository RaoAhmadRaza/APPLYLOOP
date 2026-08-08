"""What the model is allowed to return for one match. **This is the whole output surface.**

§3.3 lets the model reorder, rephrase and surface evidence, and never add a claim. The
cheapest way to enforce most of that is to give it nothing else to write: name, contact,
employers, titles, dates and education are assembled by code from `parsed_json`, so
there is no field here in which a company or a date could be invented. What remains is
bullets and a skills selection, and both are checked before anything renders.

**Every bullet cites the claim it came from, at generation time.** Asking for the
citation up front rather than matching generated text back to sources afterwards is the
difference between the validator diffing against *one* row and diffing against the whole
vault — and diffing against one row is what makes "PostgreSQL, but attached to the job
where you never used it" catchable at all. It is also the cheaper prompt.

`evidence_id` is a short handle (`E7`), not the row's UUID. The mapping lives in Python.
A UUID is ~20 tokens, the vault sends dozens of rows, and the model has to echo one back
per bullet — that is real money on the strong model, and a mistyped handle fails loudly
where a mistyped UUID looks like a different row.

**No summary field, deliberately.** A free-prose professional summary is the highest
fabrication risk on a résumé — it is the one section with no source row to point at —
and M5's gate does not ask for one.
"""

from schemas.common import Schema


class TailoredBullet(Schema):
    """One rewritten bullet and the claim it is a rewrite *of*."""

    # The handle from the supplied evidence list. Anything not on that list is stripped
    # — a bullet whose provenance we cannot name is a bullet with no provenance.
    evidence_id: str
    text: str


class TailoredResume(Schema):
    """The résumé half of one tailoring call."""

    bullets: list[TailoredBullet]
    # Selected, never written: each entry must equal a stored `skill` claim exactly.
    # §3.3 names inventing a skill as the adversarial case the permanent test must
    # catch, so this field is the one place with no rewriting allowance at all.
    skills: list[str]


class CoverLetterParagraph(Schema):
    """One body paragraph and every claim it draws on."""

    # Plural here, singular on a bullet: a paragraph legitimately joins two claims into
    # one sentence, where a bullet is a rewrite of exactly one.
    evidence_ids: list[str]
    text: str


class CoverLetterDraft(Schema):
    """The cover-letter half. Body paragraphs only.

    The greeting and the closing are written by code. They are the two parts that carry
    no evidence and are pure form, and leaving them to the model means validating prose
    that has nothing to trace to.
    """

    paragraphs: list[CoverLetterParagraph]
