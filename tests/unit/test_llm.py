"""The one LLM call, against a fake transport.

Nothing here reaches a provider. What is under test is the part that is ours: the
schema rewrite strict mode demands, and the single corrective re-ask. The model's
ability to follow a schema is measured by the live suite, not asserted here.
"""

import json
from typing import Any

import httpx
import pytest
from pydantic import Field
from schemas.common import Schema
from schemas.resume import ParsedResume
from workers import llm


class Inner(Schema):
    label: str | None = None


class Sample(Schema):
    name: str
    count: int | None = None
    tags: list[str] = Field(default_factory=list)
    inner: Inner | None = None


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key must be present or `complete_json` refuses before it opens a socket."""
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("LLM_MODEL", "test/model")
    llm.get_settings.cache_clear()


def _transport(*contents: str, seen: list[dict[str, Any]] | None = None) -> httpx.MockTransport:
    """Answers with each content in turn, recording the request bodies."""
    replies = iter(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    return httpx.MockTransport(handler)


def _patch(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = llm._client

    def fake(settings: Any) -> httpx.Client:
        client = real(settings)
        return httpx.Client(base_url=client.base_url, headers=client.headers, transport=transport)

    monkeypatch.setattr(llm, "_client", fake)


# ------------------------------------------------------------------ the schema rewrite


def test_every_object_forbids_additional_properties() -> None:
    """Strict mode rejects a schema that allows unknown keys, and pydantic never emits
    the flag. Missing it means the whole request 400s at the provider."""
    strict = llm._strict(Sample.model_json_schema())

    assert strict["additionalProperties"] is False
    assert strict["$defs"]["Inner"]["additionalProperties"] is False


def test_every_property_is_required_including_the_optional_ones() -> None:
    """Strict mode expresses optionality as a nullable type, never as absence from
    `required`. Our fields are already `X | None`, so listing them all is exactly
    correct rather than a lie about what the model must return."""
    strict = llm._strict(Sample.model_json_schema())

    assert strict["required"] == ["name", "count", "tags", "inner"]


def test_defaults_are_stripped() -> None:
    """`default` is not an allowed keyword in strict mode, and pydantic emits one per
    defaulted field."""
    strict = llm._strict(Sample.model_json_schema())

    assert "default" not in strict["properties"]["tags"]


def test_the_rewrite_does_not_mutate_pydantics_cached_schema() -> None:
    """`model_json_schema()` is cached per class. Editing the result in place would
    corrupt every later call in the process — including the next stage's."""
    original = Sample.model_json_schema()

    llm._strict(original)

    assert "additionalProperties" not in original


def test_the_real_resume_schema_survives_the_rewrite() -> None:
    """The rewrite has to hold for the model actually used, nested $defs and all."""
    strict = llm._strict(ParsedResume.model_json_schema())

    assert strict["additionalProperties"] is False
    assert strict["$defs"]["ResumeWork"]["additionalProperties"] is False
    assert "work" in strict["required"]


# ------------------------------------------------------------------------ the request


def test_the_request_carries_strict_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _patch(monkeypatch, _transport('{"name": "ok"}', seen=seen))

    llm.complete_json(Sample, system="s", user="u")

    schema = seen[0]["response_format"]["json_schema"]
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["name"] == "Sample"
    assert seen[0]["model"] == "test/model"


def test_a_valid_response_is_returned_as_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _transport('{"name": "Ada", "count": 3}'))

    result = llm.complete_json(Sample, system="s", user="u")

    assert result.name == "Ada"
    assert result.count == 3


# -------------------------------------------------------------- the corrective re-ask


def test_a_schema_violation_is_re_asked_once_with_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one thing worth keeping from `instructor`. A bare retry re-sends the same
    prompt and earns the same failure; attaching the error is what makes it converge."""
    seen: list[dict[str, Any]] = []
    _patch(monkeypatch, _transport('{"count": 1}', '{"name": "Ada"}', seen=seen))

    result = llm.complete_json(Sample, system="s", user="u")

    assert result.name == "Ada"
    assert len(seen) == 2
    correction = seen[1]["messages"][-1]["content"]
    assert "failed schema validation" in correction
    assert "name" in correction


def test_a_second_failure_raises_rather_than_returning_a_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a best-effort object. A half-parsed résumé puts wrong data in front of
    M4's free hard filter, which drops jobs silently instead of failing."""
    _patch(monkeypatch, _transport('{"count": 1}', '{"count": 2}'))

    with pytest.raises(llm.LlmError):
        llm.complete_json(Sample, system="s", user="u")


def test_the_raised_error_does_not_leak_the_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This message reaches the Celery log and the Redis result backend, and the input
    it is complaining about is a fragment of somebody's résumé (Part 13 rule 9). Field
    locations only."""
    _patch(monkeypatch, _transport('{"count": 1, "tags": ["ssn-555"]}', '{"count": 2}'))

    with pytest.raises(llm.LlmError) as caught:
        llm.complete_json(Sample, system="s", user="u")

    assert "ssn-555" not in str(caught.value)
    assert "name" in str(caught.value)


# ------------------------------------------------------------------------ the interlock


def test_no_key_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty is a safety interlock, not a missing value — the caller no-ops and records
    why, exactly as the aggregator does without a proxy."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm.get_settings.cache_clear()

    assert llm.is_configured() is False

    with pytest.raises(llm.LlmError):
        llm.complete_json(Sample, system="s", user="u")


def test_an_empty_completion_is_an_error_not_an_empty_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank response is a failed call. Letting it through would write an empty
    parsed_json over a good one."""
    _patch(monkeypatch, _transport("   "))

    with pytest.raises(llm.LlmError):
        llm.complete_json(Sample, system="s", user="u")


def test_a_malformed_envelope_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "rate limited"})

    _patch(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(llm.LlmError):
        llm.complete_json(Sample, system="s", user="u")
