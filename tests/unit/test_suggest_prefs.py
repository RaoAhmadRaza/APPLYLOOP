"""suggest_prefs.suggest() — a pure function over a ParsedResume and a fake model, no
DB and no broker. The Celery wrapper's own interlocks (no key, no parsed résumé) are
covered in test_profile_tasks.py; this is the one thing that layer doesn't reach.
"""

import pytest
from pydantic import ValidationError
from schemas.prefs import PrefsSuggestion
from schemas.resume import ParsedResume, ResumeBasics, ResumeSkill
from workers import llm
from workers.profiles import suggest_prefs

RESUME = ParsedResume(
    basics=ResumeBasics(name="Ada Lovelace", location=None),
    skills=[ResumeSkill(name="Languages", keywords=["Python", "Go"])],
)


def test_suggest_calls_the_model_with_the_prefs_suggestion_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake(schema: type[PrefsSuggestion], *, system: str, user: str) -> PrefsSuggestion:
        seen["schema"] = schema
        seen["user"] = user
        return PrefsSuggestion(titles=["Senior Backend Engineer"], must_have_keywords=["Python"])

    monkeypatch.setattr(llm, "complete_json", fake)

    result = suggest_prefs.suggest(RESUME)

    assert seen["schema"] is PrefsSuggestion
    # The résumé is on the wire to the model, not summarised away.
    assert "Ada Lovelace" in str(seen["user"])
    assert result.titles == ["Senior Backend Engineer"]
    assert result.must_have_keywords == ["Python"]


def test_suggest_returns_the_models_answer_unmodified(monkeypatch: pytest.MonkeyPatch) -> None:
    """No post-processing here — the honesty boundary is the prompt (grounded
    rephrasing, not verbatim copy, per the module docstring), not a Python filter
    re-checking the model's answer against the résumé the way the vault does."""
    suggestion = PrefsSuggestion(
        titles=["Staff Backend Engineer"],
        locations=["Remote"],
        remote_modes=[],
        must_have_keywords=["Go"],
        exclude_keywords=["sales"],
    )
    monkeypatch.setattr(llm, "complete_json", lambda *_a, **_k: suggestion)

    result = suggest_prefs.suggest(RESUME)

    assert result == suggestion


def test_must_have_keywords_is_capped_at_one() -> None:
    """`Prefs.must_have_keywords` is an AND filter — a posting must mention every
    keyword listed to survive at all. Two real skills together already excludes most
    genuinely good postings, since few descriptions happen to name both. The cap is
    on the schema, not just the prompt: a model that "helpfully" returns two must
    fail validation and retry (`llm.complete_json`'s own corrective-retry loop),
    rather than quietly shipping a suggestion that zeroes out the candidate's search."""
    with pytest.raises(ValidationError):
        PrefsSuggestion(must_have_keywords=["Flutter", "Kotlin"])
