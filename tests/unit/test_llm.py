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
from schemas.tailoring import CoverLetterDraft, TailoredResume
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
    """A key must be present or `complete_json` refuses before it opens a socket.

    The two URLs are required fields on `Settings` and nothing here connects to either.
    They are supplied rather than inherited because the suite ignores the developer's
    `.env` (see conftest) — a unit test that borrowed them from a real file would assert
    different things on different machines, which is the bug that fixture exists for.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/none")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    # The base URL is pinned beside the slug it belongs to. Pinning only the slug left
    # the URL to come from wherever it could, and on a working machine that is the
    # developer's `.env` — the same non-hermetic shape M3 found the day a real
    # LLM_API_KEY was added, and it is still leaking here despite conftest's fixture.
    # See docs/DECISIONS.md → M5.
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
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


def _tool_transport(
    *contents: str, seen: list[dict[str, Any]] | None = None
) -> httpx.MockTransport:
    """A provider that answers in a forced tool call, leaving `content` empty."""
    replies = iter(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        call = {"function": {"name": "Sample", "arguments": next(replies)}}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "", "tool_calls": [call]}}]}
        )

    return httpx.MockTransport(handler)


def _patch(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = llm.client

    def fake() -> httpx.Client:
        client = real()
        return httpx.Client(base_url=client.base_url, headers=client.headers, transport=transport)

    monkeypatch.setattr(llm, "client", fake)


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


def test_the_tailoring_schemas_survive_the_rewrite() -> None:
    """M5's two, for the same reason — and one property in particular.

    `evidence_id` is a plain string rather than a UUID, and this is the test that says
    why out loud: a `format: uuid` keyword is not part of the strict subset every
    provider agrees on, and the citation is a handle we minted anyway.
    """
    resume = llm._strict(TailoredResume.model_json_schema())
    letter = llm._strict(CoverLetterDraft.model_json_schema())

    assert resume["additionalProperties"] is False
    assert resume["$defs"]["TailoredBullet"]["additionalProperties"] is False
    assert resume["required"] == ["bullets", "skills"]
    cited = resume["$defs"]["TailoredBullet"]["properties"]["evidence_id"]
    assert cited["type"] == "string"
    assert "format" not in cited

    assert letter["$defs"]["CoverLetterParagraph"]["required"] == ["evidence_ids", "text"]


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


def test_deepseek_is_asked_with_a_forced_tool_call_not_a_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider branch in `_enforcement`, pinned against the live endpoint.

    Verified 2026-08-10: DeepSeek's `/v1` answers `This response_format type is
    unavailable now`, and `/beta` refuses a *forced* `tool_choice` while thinking is on.
    Every part of the workaround is asserted, because each one alone looks like clutter
    a later reader would tidy away — and the schema still has to arrive strict, since a
    tool call that enforces nothing is `json_object` with extra steps.
    """
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/beta")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    llm.get_settings.cache_clear()
    seen: list[dict[str, Any]] = []
    _patch(monkeypatch, _tool_transport('{"name": "ok"}', seen=seen))

    llm.complete_json(Sample, system="s", user="u")

    body = seen[0]
    assert "response_format" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"] == {"type": "function", "function": {"name": "Sample"}}
    function = body["tools"][0]["function"]
    assert function["strict"] is True
    assert function["parameters"]["additionalProperties"] is False


def test_a_provider_error_carries_its_body_and_not_just_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`400 Bad Request` reads identically for a retired model name, a crossed slug and
    an unsupported `response_format`. Only the body tells them apart, and a bare
    `raise_for_status` threw it away — which cost an afternoon of guessing once."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "This response_format type is unavailable now"}}
        )

    _patch(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(llm.LlmError, match="unavailable now"):
        llm.complete_json(Sample, system="s", user="u")


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


# ------------------------------------------------------------- the token accounting


def _usage_transport(*replies: tuple[str, dict[str, int]]) -> httpx.MockTransport:
    """Answers with (content, usage) pairs in turn."""
    answers = iter(replies)

    def handler(_request: httpx.Request) -> httpx.Response:
        content, usage = next(answers)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": usage}
        )

    return httpx.MockTransport(handler)


def test_the_usage_sink_records_what_the_call_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _usage_transport(('{"name": "ok"}', {"prompt_tokens": 12, "completion_tokens": 3})),
    )
    spent: list[llm.Usage] = []

    llm.complete_json(Sample, system="s", user="u", usage=spent)

    assert spent == [llm.Usage(prompt_tokens=12, completion_tokens=3)]


def test_a_corrective_re_ask_is_counted_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry resends the whole transcript, so it is where a silent 2x hides.

    A cost measurement that counted only the successful attempt would under-report every
    call that needed correcting — which is exactly the call that costs the most.
    """
    _patch(
        monkeypatch,
        _usage_transport(
            ("not json at all", {"prompt_tokens": 12, "completion_tokens": 2}),
            ('{"name": "ok"}', {"prompt_tokens": 40, "completion_tokens": 3}),
        ),
    )
    spent: list[llm.Usage] = []

    llm.complete_json(Sample, system="s", user="u", usage=spent)

    assert [entry.prompt_tokens for entry in spent] == [12, 40]


def test_a_failed_call_still_reports_what_it_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two failed attempts cost real money. Raising without recording hides the bill."""
    _patch(
        monkeypatch,
        _usage_transport(
            ("nope", {"prompt_tokens": 12, "completion_tokens": 2}),
            ("still nope", {"prompt_tokens": 40, "completion_tokens": 2}),
        ),
    )
    spent: list[llm.Usage] = []

    with pytest.raises(llm.LlmError):
        llm.complete_json(Sample, system="s", user="u", usage=spent)

    assert len(spent) == llm.MAX_ATTEMPTS


def test_omitting_the_sink_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """M3 passes no sink. The parameter must stay invisible to every existing caller."""
    _patch(
        monkeypatch,
        _usage_transport(('{"name": "ok"}', {"prompt_tokens": 12, "completion_tokens": 3})),
    )

    assert llm.complete_json(Sample, system="s", user="u").name == "ok"


def test_a_provider_that_omits_usage_yields_zeros_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A good completion must not fail because the cost measurement was unavailable."""
    _patch(monkeypatch, _transport('{"name": "ok"}'))
    spent: list[llm.Usage] = []

    llm.complete_json(Sample, system="s", user="u", usage=spent)

    assert spent == [llm.Usage(prompt_tokens=0, completion_tokens=0)]


# ------------------------------------------------------------------------- embeddings


def _embedding_transport(
    *vectors: list[float], seen: list[dict[str, Any]] | None = None, shuffle: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        rows = [{"index": n, "embedding": vector} for n, vector in enumerate(vectors)]
        return httpx.Response(
            200,
            json={
                "data": list(reversed(rows)) if shuffle else rows,
                "usage": {"prompt_tokens": 99},
            },
        )

    return httpx.MockTransport(handler)


def test_embed_returns_vectors_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _patch(monkeypatch, _embedding_transport([1.0, 2.0], [3.0, 4.0], seen=seen))

    vectors, tokens = llm.embed(["a", "b"])

    assert vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert tokens == 99
    assert seen[0]["input"] == ["a", "b"]


def test_embed_reorders_by_index_rather_than_trusting_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misordered batch pairs job A's text with job B's vector.

    Nothing downstream can detect that: every cosine still computes, the shortlist still
    has N entries, and every score is quietly about the wrong posting.
    """
    _patch(monkeypatch, _embedding_transport([1.0, 2.0], [3.0, 4.0], shuffle=True))

    vectors, _ = llm.embed(["a", "b"])

    assert vectors == [[1.0, 2.0], [3.0, 4.0]]


def test_embed_refuses_a_length_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer vectors than texts would silently shift every later pairing by one."""
    _patch(monkeypatch, _embedding_transport([1.0, 2.0]))

    with pytest.raises(llm.LlmError):
        llm.embed(["a", "b"])


def test_embedding_nothing_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "The hard filters left nothing" is a normal run, not an error, and must not
    open a socket or spend a token."""

    def explode(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("embed([]) must not reach the provider")

    _patch(monkeypatch, httpx.MockTransport(explode))

    assert llm.embed([]) == ([], 0)
