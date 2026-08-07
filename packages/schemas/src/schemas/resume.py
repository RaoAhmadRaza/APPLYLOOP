"""`profiles.parsed_json` — the structured résumé M4 and M5 read.

A subset of the **JSON Resume** standard (jsonresume.org, MIT), not a shape invented
here. Three reasons: it is the community format every OSS résumé tool already speaks,
its entry shapes line up with RenderCV's — §7.2's chosen renderer for M5 — and using a
published vocabulary means a future import/export path is a mapping rather than a
rewrite. Dropped from the standard: volunteer, awards, publications, languages,
interests, references, meta. Nothing downstream consumes them; add one when something
does.

**Every field is optional or defaulted, without exception.** This is not defensive
style, it is the documented failure mode of the closest prior art: `srbhr/Resume-Matcher`
raises `1 validation error for ResumeData` on any résumé missing a Projects section,
because a senior CV routinely omits one. A parser that refuses a real résumé is worse
than one that returns a sparse record.

Dates are **strings**, not `date`. A résumé says "2021 – Present", and a real end date
of `None` means "still there" — which a `date` cannot express and a sentinel would lie
about. The model is constrained to `YYYY` or `YYYY-MM`; `derive.py` parses that
leniently, and a malformed value trips the LLM client's corrective retry rather than
silently becoming 1970.

`years_experience` is **derived by Python, never asked of the model** — see derive.py.
It sits here because it is part of the record M4 reads, not because the parser produced
it.
"""

from typing import Annotated

from pydantic import Field, StringConstraints

from schemas.common import Schema

# `YYYY` or `YYYY-MM`. Anything else is a parse error the model gets one chance to fix,
# rather than a date that silently reads as the wrong century.
RESUME_DATE_PATTERN = r"^\d{4}(-\d{2})?$"

ResumeDate = Annotated[str, StringConstraints(pattern=RESUME_DATE_PATTERN)]


class ResumeLocation(Schema):
    city: str | None = None
    region: str | None = None
    country_code: str | None = None


class ResumeBasics(Schema):
    name: str | None = None
    label: str | None = None
    email: str | None = None
    phone: str | None = None
    url: str | None = None
    summary: str | None = None
    location: ResumeLocation | None = None


class ResumeWork(Schema):
    # JSON Resume calls the employer `name`, not `company`. Kept as-is: diverging from
    # the standard on the one field every consumer looks up buys nothing.
    name: str | None = None
    position: str | None = None
    location: str | None = None
    start_date: ResumeDate | None = None
    # None means "present". That is the whole reason these are strings.
    end_date: ResumeDate | None = None
    summary: str | None = None
    # The bullets. These become `evidence` rows of kind `bullet`, so this is the field
    # M5's validator ultimately traces a generated bullet back to.
    highlights: list[str] = Field(default_factory=list)


class ResumeEducation(Schema):
    institution: str | None = None
    area: str | None = None
    study_type: str | None = None
    start_date: ResumeDate | None = None
    end_date: ResumeDate | None = None


class ResumeSkill(Schema):
    name: str | None = None
    keywords: list[str] = Field(default_factory=list)


class ResumeProject(Schema):
    name: str | None = None
    description: str | None = None
    highlights: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    start_date: ResumeDate | None = None
    end_date: ResumeDate | None = None
    url: str | None = None


class ResumeCertificate(Schema):
    name: str | None = None
    issuer: str | None = None
    date: ResumeDate | None = None


class ParsedResume(Schema):
    """What the extractor returns, and what `profiles.parsed_json` holds."""

    basics: ResumeBasics = Field(default_factory=ResumeBasics)
    work: list[ResumeWork] = Field(default_factory=list)
    education: list[ResumeEducation] = Field(default_factory=list)
    skills: list[ResumeSkill] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    certificates: list[ResumeCertificate] = Field(default_factory=list)

    # Derived from `work` by merging overlapping date ranges — not extracted. Two
    # concurrent roles are one stretch of experience, and asking a model to do that
    # arithmetic makes a number M4 filters on vary between runs.
    years_experience: float | None = None

    # Stated only when the résumé actually says so ("authorized to work in the US
    # without sponsorship"). Most résumés do not, and NULL is the honest answer —
    # never a guess. `derive.py` maps this onto the WorkAuth enum.
    work_authorization: str | None = None
