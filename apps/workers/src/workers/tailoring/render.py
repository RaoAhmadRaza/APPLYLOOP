"""Validated content -> PDF. Deterministic, and the model never sees this file's output.

§7.2 chose RenderCV over "the LLM emits a formatted document" because model output that
*is* the layout is unreviewable: there is no diff between two runs that means anything,
and no way to assert that a field is present. Here the layout is a function of a dict.

**RenderCV is driven through its CLI, not its Python API.** Its own documentation says
"RenderCV is a CLI application, not a library. Its internal API is not guaranteed to be
stable and may change without notice." The YAML input and the CLI are the contract it
does support, so that is the one used — through `sys.executable -m rendercv`, which needs
no console script on PATH and therefore behaves the same in the container and out of it.

Two flags matter and both were learned by running it: `-notyp` implicitly disables the
PDF as well (Typst is the intermediate), and `--pdf-path` is relative to the *input file*
rather than to the working directory.

The cover letter goes through Typst directly. RenderCV renders CVs and a letter is not
one; a 20-line template compiled by the same engine costs less than bending a CV theme
into a letter, and `typst` is already installed because RenderCV needs it.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import typst
from schemas.resume import ParsedResume
from schemas.tailoring import TailoredBullet

from workers.tailoring.validate import Vault

# The theme is engineeringresumes for the reason every theme would do: they are all
# single-column, which is the one layout property that measurably breaks ATS parsers.
THEME = "engineeringresumes"

# A render is CPU-bound and local — no network, no model. If it has not finished in this
# long the Typst compiler is wedged, and a worker blocked forever is worse than a failure.
TIMEOUT_SECONDS = 120


class RenderError(RuntimeError):
    """The renderer refused. Never partially written output."""


def resume_pdf(
    *, resume: ParsedResume, bullets: list[TailoredBullet], skills: list[str], vault: Vault
) -> bytes:
    """Render the validated bullets back into the résumé's own structure.

    Bullets are re-attached under the role their evidence came from, read off the claim's
    `source` pointer (`work[1].highlights[0]`). That is not bookkeeping — it is the last
    line of defence for BAR.md §4's skill-role misalignment: a bullet physically cannot
    land under a different employer than the claim it cites, because the pointer decides
    and no model output is involved.

    Roles with no surviving bullet are still listed. Employment history with a gap in it
    is a different lie from the one this milestone is about.
    """
    grouped = group_by_owner(bullets, vault)

    sections: dict[str, list[dict[str, object]]] = {}

    experience = [
        _entry(
            {"company": work.name or "", "position": work.position or ""},
            work.start_date,
            work.end_date,
            grouped.get(f"work[{index}]", []),
        )
        for index, work in enumerate(resume.work)
        if work.name or work.position
    ]
    if experience:
        sections["experience"] = experience

    projects = [
        _entry(
            {"name": project.name or ""},
            project.start_date,
            project.end_date,
            grouped.get(f"projects[{index}]", []),
        )
        for index, project in enumerate(resume.projects)
        if project.name and grouped.get(f"projects[{index}]")
    ]
    if projects:
        sections["projects"] = projects

    if skills:
        # One line, in the order the model selected. Reconstructing the résumé's own skill
        # groupings is possible and speculative: the group labels are themselves claims,
        # so a "group" is only recoverable by guessing which atoms belonged to it.
        sections["skills"] = [{"label": "Skills", "details": ", ".join(_members(skills))}]

    education = [
        _entry(
            {
                "institution": entry.institution or "",
                "area": entry.area or "",
                **({"degree": entry.study_type} if entry.study_type else {}),
            },
            entry.start_date,
            entry.end_date,
            [],
        )
        for entry in resume.education
        if entry.institution
    ]
    if education:
        sections["education"] = education

    certificates: list[dict[str, object]] = [
        {"label": entry.name, "details": entry.issuer or ""}
        for entry in resume.certificates
        if entry.name
    ]
    if certificates:
        sections["certifications"] = certificates

    document = {
        "cv": {**_contact(resume), "sections": sections},
        "design": {"theme": THEME},
    }
    return _render(document)


def _members(skills: list[str]) -> list[str]:
    """Drop a group label from a selected skill, keeping its members.

    When M3's parse leaves a skills line whole, the vault's claim is
    `Languages: Python, Go, SQL` and that is what the model can legally select. Printed
    raw it renders as `Skills: Languages: Python, Go, SQL, Infrastructure: Kubernetes…`,
    which is what M5's first real document actually said.

    A display transform over already-validated text, deliberately: these strings passed
    the skills rule by exact membership before they got here, so reformatting cannot
    admit anything. Duplicates are dropped because two selected groups can share a tool.
    """
    seen: dict[str, None] = {}
    for skill in skills:
        _, _, tail = skill.rpartition(":")
        for member in (tail or skill).split(","):
            cleaned = member.strip()
            if cleaned:
                seen.setdefault(cleaned, None)
    return list(seen)


def group_by_owner(bullets: list[TailoredBullet], vault: Vault) -> dict[str, list[str]]:
    """Bullet texts, keyed by the résumé entry their evidence came from.

    Public because it *is* the skill-role misalignment defence, and a property worth
    asserting on directly rather than through a PDF: which role a bullet appears under is
    decided by the claim's `source` pointer, with no model output anywhere in the path.
    """
    grouped: dict[str, list[str]] = {}
    for bullet in bullets:
        claim = vault.claims[bullet.evidence_id]
        owner = claim.source.split(".", 1)[0]  # "work[1].highlights[0]" -> "work[1]"
        grouped.setdefault(owner, []).append(bullet.text)
    return grouped


def cover_letter_pdf(
    *, name: str, company: str, title: str, paragraphs: list[str], today: str
) -> bytes:
    """Compile the validated paragraphs into a letter.

    Every paragraph is inserted as a Typst *string expression* rather than as markup, so
    a résumé that says `C#`, `*nix` or `@scale` cannot become a heading, an emphasis or a
    reference. Escaping markup is the kind of thing that works until somebody's job title
    contains a plus sign.
    """
    body = "\n\n".join(f"#({json.dumps(paragraph)})" for paragraph in paragraphs)
    source = f"""#set page(margin: 2cm)
#set text(size: 11pt)
#set par(justify: false, leading: 0.65em)

#({json.dumps(name)})

#({json.dumps(today)})

#({json.dumps(company)})

Dear Hiring Team,

{body}

Sincerely,

#({json.dumps(name)})
"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / "letter.typ").write_text(source)
        try:
            typst.compile(str(work / "letter.typ"), output=str(work / "letter.pdf"))
        except Exception as error:  # typst raises its own error types
            raise RenderError(f"typst refused the letter: {str(error)[:300]}") from error
        return (work / "letter.pdf").read_bytes()


def _contact(resume: ParsedResume) -> dict[str, object]:
    basics = resume.basics
    place = basics.location
    parts = [part for part in (place.city, place.region) if part] if place else []
    contact: dict[str, object] = {"name": basics.name or "Candidate"}
    if parts:
        contact["location"] = ", ".join(parts)
    if basics.email:
        contact["email"] = basics.email
    if basics.phone:
        contact["phone"] = basics.phone
    return contact


def _entry(
    fields: dict[str, object], start: str | None, end: str | None, highlights: list[str]
) -> dict[str, object]:
    """One RenderCV entry. Dates are omitted rather than guessed when the résumé had none.

    A missing date is a real state — `derive.py` already refuses to invent one — and
    RenderCV treats an absent `start_date` as an undated entry rather than an error.
    """
    entry = dict(fields)
    if start:
        entry["start_date"] = start
        entry["end_date"] = end or "present"
    if highlights:
        entry["highlights"] = highlights
    return entry


def _render(document: dict[str, object]) -> bytes:
    """Write the input file, run the CLI, read the PDF back.

    The input is written as JSON, which is valid YAML 1.2 and saves both a dependency on
    a YAML writer and a whole class of quoting bug: a bullet beginning with `-` or
    containing `: ` is a YAML landmine and an unremarkable JSON string.
    """
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / "cv.yaml").write_text(json.dumps(document, ensure_ascii=False))

        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, paths we created
            [
                sys.executable,
                "-m",
                "rendercv",
                "render",
                str(work / "cv.yaml"),
                "--pdf-path",
                "out.pdf",
                "-nomd",
                "-nohtml",
                "-nopng",
                "-q",
            ],
            capture_output=True,
            text=True,
            cwd=work,
            timeout=TIMEOUT_SECONDS,
        )

        pdf = work / "out.pdf"
        if result.returncode != 0 or not pdf.exists():
            # Truncated, and it still may contain résumé text — the same privacy problem
            # llm.py has with a validation error, handled the same way. Part 13 rule 9.
            detail = (result.stderr or result.stdout).strip()[-400:]
            raise RenderError(f"rendercv exited {result.returncode}: {detail}")
        return pdf.read_bytes()
