"""The gate's first clause: the PDF opens, and an ATS parser reads the fields back.

Free and offline — RenderCV compiles locally with no network and no model — so this runs
in CI on every push rather than only when someone spends money. That is the whole reason
the renderer is deterministic: a layout that is a function of a dict can be asserted on.

The extractor is `markitdown`, which is not chosen for convenience: it is the exact
extractor M3 parses uploaded résumés with. A field this repo cannot read out of its own
generated PDF is a field it would fail to read off a candidate's.

**Presence is asserted, adjacency is not.** PDF text extraction returns this document's
date ranges away from the roles they belong to. That is a property of extraction, not of
the document — BAR.md §3 pins it so nobody later "fixes" the renderer to chase it.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from markitdown import MarkItDown
from schemas.resume import ParsedResume
from schemas.tailoring import TailoredBullet
from workers.tailoring import render, validate
from workers.text import squash

EVALS = Path(__file__).resolve().parents[2] / "evals" / "fabrication"
VAULTS: dict[str, Any] = json.loads((EVALS / "vaults.json").read_text())["vaults"]
PROFILES: dict[str, Any] = json.loads((EVALS.parent / "golden" / "pairs.json").read_text())[
    "profiles"
]

RENDERED = ["senior_backend.pdf", "two_column.pdf"]


def _extract(pdf: bytes, tmp_path: Path) -> str:
    out = tmp_path / "rendered.pdf"
    out.write_bytes(pdf)
    return MarkItDown().convert(str(out)).text_content


@pytest.fixture(scope="module")
def rendered() -> dict[str, tuple[bytes, ParsedResume, list[str]]]:
    """Render each fixture once. Every bullet in the vault, so the read-back is over the
    widest document the fixture can produce rather than a convenient subset."""
    out = {}
    for name in RENDERED:
        blob = VAULTS[name]
        vault = validate.vault_from(blob)
        resume = ParsedResume.model_validate(PROFILES[name]["parsed_json"])
        bullets = [
            TailoredBullet(evidence_id=claim["id"], text=claim["text"])
            for claim in blob["claims"]
            if claim["kind"] == "bullet"
        ]
        skills = [claim["text"] for claim in blob["claims"] if claim["kind"] == "skill"][:6]
        pdf = render.resume_pdf(resume=resume, bullets=bullets, skills=skills, vault=vault)
        out[name] = (pdf, resume, skills)
    return out


@pytest.mark.parametrize("name", RENDERED)
def test_the_pdf_opens(name: str, rendered: dict[str, Any], tmp_path: Path) -> None:
    pdf, _, _ = rendered[name]

    assert pdf.startswith(b"%PDF-"), "not a PDF at all"
    assert _extract(pdf, tmp_path).strip(), "a PDF with no extractable text is unreadable"


@pytest.mark.parametrize("name", RENDERED)
def test_an_ats_parser_reads_every_field_back(
    name: str, rendered: dict[str, Any], tmp_path: Path
) -> None:
    """BAR.md §2's read-back bar, at 1.00, over the fields §3 pins."""
    pdf, resume, skills = rendered[name]
    haystack = squash(_extract(pdf, tmp_path))

    expected: list[tuple[str, str]] = [("name", resume.basics.name or "")]
    expected += [("company", work.name or "") for work in resume.work if work.name]
    expected += [("title", work.position or "") for work in resume.work if work.position]
    expected += [("skill", skill) for skill in skills]
    expected += [
        ("bullet", claim["text"][:40])
        for claim in VAULTS[name]["claims"]
        if claim["kind"] == "bullet"
    ]

    missing = [(kind, value) for kind, value in expected if squash(value) not in haystack]

    print(f"\n  {name}: {len(expected) - len(missing)}/{len(expected)} fields read back")
    assert not missing, f"{name} lost fields the employer would never see: {missing}"


def test_a_bullet_lands_under_the_role_its_evidence_came_from() -> None:
    """The last line of defence against BAR.md §4's skill-role misalignment.

    Asserted on the grouping rather than through the PDF, because this is a property of
    the mapping and PDF extraction cannot express adjacency anyway. The point is that the
    claim's `source` pointer decides where a bullet appears — no model output is in the
    path, so a bullet physically cannot land under an employer it does not belong to,
    even if the model wanted it to.
    """
    name = "senior_backend.pdf"
    vault = validate.vault_from(VAULTS[name])

    # E20 is Tessellate Labs, the third and most junior role. E13 is the current one.
    grouped = render.group_by_owner(
        [
            TailoredBullet(evidence_id="E20", text="Shipped a public REST API"),
            TailoredBullet(evidence_id="E13", text="Cut p95 checkout latency"),
        ],
        vault,
    )

    assert grouped == {
        "work[2]": ["Shipped a public REST API"],
        "work[0]": ["Cut p95 checkout latency"],
    }


def test_the_letter_renders_and_cannot_be_turned_into_markup(tmp_path: Path) -> None:
    """A paragraph is inserted as a Typst string, never as markup.

    `C#`, `*nix` and `@scale` are things people genuinely have on résumés, and each is
    Typst syntax. Escaping them is the kind of fix that works until the next character.
    """
    paragraph = "I have shipped C# and *nix tooling @scale, with 100% #test coverage."
    pdf = render.cover_letter_pdf(
        name="Marisol Okonkwo-Brennan",
        company="Northwind Logistics",
        title="Senior Platform Engineer",
        paragraphs=[paragraph],
        today="9 August 2026",
    )

    text = _extract(pdf, tmp_path)

    assert pdf.startswith(b"%PDF-")
    assert squash(paragraph) in squash(text), "the letter's own words did not survive"
    assert "Northwind Logistics" in text
    assert "Marisol Okonkwo-Brennan" in text
