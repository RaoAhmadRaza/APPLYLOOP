"""Résumé bytes to Markdown, over the committed fixtures.

No model and no network. What is asserted is only that the converter recovers the text
a parser would need — the *quality* of the resulting extraction is measured by the live
suite, which is where a two-column regression would actually show up.

The fixtures are synthetic and were generated once; see tests/fixtures/resumes/labels.json
for what trap each one carries.
"""

import json
from pathlib import Path

import pytest
from workers.profiles import extract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "resumes"
LABELS = json.loads((FIXTURES / "labels.json").read_text())
RESUMES = sorted(name for name in LABELS if not name.startswith("_"))


def load(name: str) -> str:
    return extract.to_markdown((FIXTURES / name).read_bytes(), Path(name).suffix)


@pytest.mark.parametrize("name", RESUMES)
def test_every_fixture_produces_text(name: str) -> None:
    """Zero characters is the silent failure this file exists for: an empty extraction
    that reached the model would come back as a profile with no experience, which reads
    as a fact rather than as a failure."""
    assert len(load(name)) > 200


@pytest.mark.parametrize("name", RESUMES)
def test_the_labelled_skills_survive_extraction(name: str) -> None:
    """If a skill is not in the text, no model can extract it and no vault can verify
    it. This pins the converter, not the parser."""
    text = load(name).lower()

    for skill in LABELS[name]["skills_include"]:
        assert skill.lower() in text, f"{name} lost {skill}"


@pytest.mark.parametrize("name", RESUMES)
def test_the_work_authorisation_sentence_survives(name: str) -> None:
    """The one M3 gate field a résumé states in prose rather than in a heading, and the
    one most likely to be dropped by a converter that discards a stray line."""
    text = load(name).lower()

    assert "sponsorship" in text or "citizen" in text


def test_a_two_column_layout_still_yields_both_columns() -> None:
    """The documented pdfminer.six weakness. This fixture is a genuine two-frame PDF.

    Asserts presence, not order: the reading order of a multi-column PDF depends on the
    producer's content stream, and this fixture's happens to be column-major. What must
    never happen is a whole column going missing — half a résumé is worse than a jumbled
    one, because the vault would then reject every true claim from it.
    """
    text = load("two_column.pdf")

    assert "Jagiellonian University" in text
    assert "Nordlys Retail Group" in text


def test_bullet_glyphs_that_do_not_map_to_unicode_are_survivable() -> None:
    """reportlab embeds the bullet as an unmapped glyph, so pdfminer emits `(cid:127)`.
    Real PDFs do this too. It is harmless because the vault's comparison drops
    everything that is not a letter or a digit — asserted here so nobody "fixes" it by
    loosening that normaliser."""
    from workers.profiles import vault

    text = load("senior_backend.pdf")
    claim = "Led the migration of 41 services from Nomad to Kubernetes across two regions"

    assert vault.is_supported(claim, text)


def test_an_unsupported_suffix_is_refused() -> None:
    with pytest.raises(extract.ExtractError):
        extract.to_markdown(b"data", ".pages")


def test_an_empty_file_raises_rather_than_returning_an_empty_string() -> None:
    """A scanned PDF with no text layer lands here."""
    with pytest.raises(extract.ExtractError):
        extract.to_markdown(b"", ".txt")


def test_the_label_set_covers_every_committed_fixture() -> None:
    """A fixture with no labels is a file nothing runs against. Catches the drift where
    someone adds a résumé and forgets the live suite exists."""
    committed = {path.name for path in FIXTURES.iterdir() if path.name != "labels.json"}

    assert committed == set(RESUMES)
