"""The one LLM call, for every stage that needs one.

Worker infrastructure, alongside `settings.py` and `app.py` — deliberately **not** inside
a stage package. M3 parses résumés, M4 explains scores and M5 tailors documents, and
§3.1 forbids `workers/matching/` importing `workers/profiles/`. A stage-local client
would therefore have to be copied three times.

**No SDK and no `instructor`.** An OpenAI-compatible `/chat/completions` call is one
POST; `httpx` is already a dependency; Celery already supplies backoff and jitter.
`instructor`'s real value is unified retry across fifteen providers, and there is one
here. What is worth keeping from it — re-asking with the validation error attached — is
the ten lines below.

**Structured output is enforced server-side, then validated client-side.** `strict: true`
constrains generation to the schema at the provider; pydantic is the belt for the braces.
Note the schema has to be *rewritten* for strict mode — see `_strict`.

`trust_env=False` for the same reason `scraping/http.py` sets it: Part 13 rule 12 scopes
the residential proxy to the aggregator layer, and routing model traffic through metered
bandwidth would be an expensive accident caused by an env var nobody read.

**The line this module draws, stated once:** anything that talks to the model provider
lives here; anything that talks to Postgres lives in the stage. That is why `embed()` is
here and the `job_embeddings` write is not.

**Chat and embeddings are two providers, not one.** They shared a client until DeepSeek —
which serves no `/embeddings` route at all — became the chat provider. `_client()` takes
the host and key it is given; `embed_url` / `embed_key` on `Settings` fall back to the
chat pair, so a single-provider deployment sets nothing new.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from schemas.job_embedding import EMBEDDING_DIM

from workers.settings import get_settings

# One corrective re-ask, never a loop. If a schema-constrained model cannot satisfy its
# own schema twice, the prompt is wrong and burning credit will not fix it. Celery's
# retries sit above this and handle transport failures.
MAX_ATTEMPTS = 2

# Tries per embedding batch against a rate limit, and *only* against a rate limit.
#
# A token-per-minute ceiling is arithmetic, not an incident: a cold pool of 8,871 jobs
# needs several million tokens and the limit is a million a minute, so a first run is
# refused partway through by construction. The provider says how long to wait — measured
# 2026-08-19, `Please try again in 1.002s` — so waiting is the whole remedy, and six
# tries spans more than the one-minute window the limit resets on. Bounded rather than
# indefinite so a genuinely capped key fails loudly instead of sleeping forever.
RATE_LIMIT_ATTEMPTS = 6

# Sent by OpenRouter as attribution. Honest identification, same policy as the scraper's
# User-Agent: these are public endpoints and we are not pretending to be a browser.
_REFERER = "https://github.com/RaoAhmadRaza/APPLYLOOP"
_TITLE = "APPLYLOOP"


class LlmError(RuntimeError):
    """The model could not produce a valid response.

    Raised rather than returning a partial object: a half-parsed résumé puts wrong data
    in front of M4's *free* hard filter, which drops jobs silently — the same reasoning
    that keeps `is_remote=False` mapping to NULL instead of ONSITE.
    """


@dataclass(frozen=True)
class Usage:
    """What one request cost, in the only unit the provider actually reports.

    Tokens rather than dollars: a price is a fact about a contract that changes without
    notice, a token count is a fact about the request. §8.2 wants per-run spend, and the
    conversion belongs where the price literal sits next to the model name — the eval,
    and M11's dashboards. Storing dollars in `events` would freeze a stale rate into the
    audit trail.
    """

    prompt_tokens: int
    completion_tokens: int


def is_configured() -> bool:
    """The interlock. No key means the caller no-ops and says so, never guesses."""
    return get_settings().llm_api_key is not None


def complete_json[T: BaseModel](
    schema: type[T],
    *,
    system: str,
    user: str,
    usage: list[Usage] | None = None,
    model: str | None = None,
) -> T:
    """One structured completion, validated into `schema`.

    Raises `LlmError` if the model fails the schema twice.

    `usage` is an optional caller-owned sink rather than a second return value, because
    changing the return type would break every existing call site for a number most of
    them do not want. **Appended once per attempt, including the attempt that raises** —
    a corrective re-ask resends the whole transcript, so it is exactly where a silent 2×
    would hide from a cost measurement.

    `model` overrides `LLM_MODEL` for this call. §7.2 decided scoring gets a cheap model
    and tailoring gets a strong one, routed — one key through OpenRouter, two slugs. The
    override lives here rather than in a second client because everything else about the
    call is identical, and a second client is a second place for the strict-schema
    rewrite and the usage sink to drift.
    """
    settings = get_settings()
    if settings.llm_api_key is None:
        raise LlmError("no LLM API key configured")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    body_schema = _strict(schema.model_json_schema())

    with client() as http:
        for attempt in range(MAX_ATTEMPTS):
            content = _ask(
                http, model or settings.llm_model, messages, schema.__name__, body_schema, usage
            )
            try:
                return schema.model_validate_json(content)
            except ValidationError as error:
                if attempt == MAX_ATTEMPTS - 1:
                    # Field locations only, never the errors' `input` values — those are
                    # fragments of somebody's résumé and this string reaches the Celery
                    # log and the Redis result backend (Part 13 rule 9).
                    locations = [item["loc"] for item in error.errors()]
                    raise LlmError(f"response failed validation at {locations}") from error
                # Re-ask with the failure attached. The model already holds the source
                # text, so the full error costs nothing extra in privacy terms here.
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "That response failed schema validation:\n"
                            f"{error}\n"
                            "Return corrected JSON. Change nothing else."
                        ),
                    },
                ]
    raise LlmError("unreachable")  # pragma: no cover — the loop always returns or raises


def embed(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed a batch. Returns the vectors in input order, and the tokens they cost.

    A tuple rather than the `usage` sink `complete_json` takes: no existing caller whose
    signature has to survive, so the smaller diff wins.

    **Retries a 429, and nothing else.** The comment this replaces said Celery's backoff
    sat above and made a loop here unnecessary; that was wrong twice over. A 429 leaves
    here as `LlmError`, which is a `RuntimeError` and so never matched
    `match_profile`'s `autoretry_for=(httpx.HTTPError,)` — there was no retry at any
    level. And a task-level retry is the wrong altitude anyway: it re-runs the
    candidate query and the scoring loop to recover one batch the provider asked us to
    wait a second for.

    Raises `LlmError` if the provider returns a different number of vectors than it was
    given — a length mismatch would silently pair job A's text with job B's vector, and
    every downstream cosine would be wrong in a way nothing else can detect.
    """
    settings = get_settings()
    key = settings.embed_key
    if key is None:
        raise LlmError("no embeddings API key configured")
    if not texts:
        # Not an error, and deliberately before the client is opened: M4 calls this with
        # whatever the hard filters left, and "nothing survived" is a normal run.
        return [], 0

    # `embed_url`, not `llm_base_url`: embeddings may live at another provider entirely,
    # and since 2026-08-10 they do — DeepSeek serves no /embeddings route.
    with _client(settings.embed_url, key) as http:
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            response = http.post(
                "/embeddings",
                json={
                    "model": settings.embed_model,
                    "input": texts,
                    # Asked for, never assumed. `gemini-embedding-001` returns 3072 by
                    # default and the column is EMBEDDING_DIM wide, so taking the default
                    # would fail every insert. Stating the width we require also documents
                    # it at the one place the request is made — `_checked()` still
                    # verifies what came back.
                    "dimensions": EMBEDDING_DIM,
                },
            )
            if response.status_code != 429 or attempt == RATE_LIMIT_ATTEMPTS - 1:
                break
            time.sleep(_retry_after(response, attempt))
    _raise_for_body(response)
    payload = response.json()

    try:
        # Sorted by `index`, not trusted to arrive in order. The spec says ordered; a
        # misordered batch is indistinguishable from a correct one at every later step.
        #
        # `.get(..., 0)` because **Gemini omits `index` entirely when it is 0** — proto3
        # drops default-valued fields, so row 0 arrives without the key and row 1 carries
        # `index: 1`. `row["index"]` raised on the very first real batch. Only the zero can
        # be missing, so defaulting to 0 restores the true order rather than guessing at it.
        rows = sorted(payload["data"], key=lambda row: row.get("index", 0))
        vectors = [row["embedding"] for row in rows]
    except (KeyError, IndexError, TypeError) as error:
        raise LlmError(f"unexpected embeddings response shape: {sorted(payload)}") from error

    if len(vectors) != len(texts):
        raise LlmError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
    # **Gemini sends no `usage` object at all**, so this is 0 on every embed call there —
    # not a stub, a provider that does not report it. M4's live gate asserts
    # `embed_tokens > 0` as its anti-stub proof and will fail on that alone; see
    # docs/PROJECT_STATE.md. `or {}` because absent and null must behave the same.
    return vectors, int((payload.get("usage") or {}).get("prompt_tokens", 0))


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """How long the provider asked us to wait, falling back to doubling.

    Its own number beats ours: it knows when the window rolls over and we are guessing.
    A malformed header falls back rather than raising, because failing to parse a hint
    is not a reason to abandon a batch that would have succeeded a second later.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return float(2**attempt)


def client() -> httpx.Client:
    """The chat client."""
    settings = get_settings()
    assert settings.llm_api_key is not None  # noqa: S101 — the caller checked
    return _client(settings.llm_base_url, settings.llm_api_key)


def _client(base_url: str, api_key: SecretStr) -> httpx.Client:
    """One factory, two hosts. Chat and embeddings no longer have to be the same provider.

    The attribution headers are gated rather than always sent: they are OpenRouter's, the
    comment above them has always said so, and sending this repo's URL and name to Google
    or DeepSeek tells a third party who we are in exchange for nothing.
    """
    headers = {
        "Authorization": f"Bearer {api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in base_url:
        headers |= {"HTTP-Referer": _REFERER, "X-Title": _TITLE}
    return httpx.Client(
        base_url=base_url,
        timeout=get_settings().llm_timeout_seconds,
        headers=headers,
        # Part 13 rule 12: the proxy is the aggregator's, and an ambient HTTPS_PROXY
        # would silently bill model traffic to residential bandwidth.
        trust_env=False,
    )


def _ask(
    http: httpx.Client,
    model: str,
    messages: list[dict[str, str]],
    name: str,
    schema: dict[str, Any],
    usage: list[Usage] | None = None,
) -> str:
    response = http.post(
        "/chat/completions",
        json={"model": model, "messages": messages, **_enforcement(name, schema)},
    )
    _raise_for_body(response)
    payload = response.json()
    if usage is not None:
        # Recorded before the content is validated, so a response that fails the schema
        # still shows up in the bill. A provider that omits `usage` yields zeros rather
        # than raising: the cost measurement is not worth failing a good completion over.
        counts = payload.get("usage") or {}
        usage.append(
            Usage(
                prompt_tokens=int(counts.get("prompt_tokens", 0)),
                completion_tokens=int(counts.get("completion_tokens", 0)),
            )
        )
    try:
        message = payload["choices"][0]["message"]
        # A forced tool call puts the JSON in `arguments` and leaves `content` empty —
        # same schema, same server-side enforcement, different envelope. Read it second
        # so nothing changes for a provider that answers the ordinary way.
        content = message.get("content") or _tool_arguments(message)
    except (KeyError, IndexError, TypeError) as error:
        raise LlmError(f"unexpected response shape: {sorted(payload)}") from error
    if not isinstance(content, str) or not content.strip():
        raise LlmError("model returned empty content")
    return content


def _raise_for_body(response: httpx.Response) -> None:
    """The body, not just the code.

    A bare `raise_for_status` cost a whole afternoon: `400 Bad Request` reads identically
    for a retired model name, a crossed vendor slug and an unsupported `response_format`,
    and this repo has now hit all three. Only the body of the last one says
    `This response_format type is unavailable now`.

    Shared by both paths deliberately. Chat was fixed first and embeddings kept the old
    shape for a day, which is exactly how the next instance of this would have happened —
    a Gemini 400 on a model name, read as an outage.
    """
    if response.is_error:
        raise LlmError(f"{response.status_code} from {response.url}: {response.text[:400]}")


def _tool_arguments(message: dict[str, Any]) -> str:
    """The JSON out of a forced tool call, or `""` if there wasn't one."""
    calls = message.get("tool_calls") or []
    return calls[0]["function"]["arguments"] if calls else ""


def _enforcement(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """How this provider is asked for the schema, and how much that asking is worth.

    **The two branches are not equally strong, and the weaker one is not a fallback — it
    is the only thing DeepSeek offers.** Verified against the live endpoints 2026-08-10:

      * OpenAI and OpenRouter take `response_format: json_schema` and genuinely
        constrain decoding. The module docstring's "enforced server-side" is true there.
      * **DeepSeek refuses it** — `This response_format type is unavailable now`, which
        is what `.env.example` recorded before any of this was written. Its strict
        schemas live on *tool* definitions behind the `/beta` host.
      * **DeepSeek's `strict: true` does not constrain generation.** It validates the
        schema you register, then the model writes what it likes. Asked to violate its
        own tool schema it returned `{"city": ["Paris", "Lyon"], "population": many}` —
        a list where the schema says string, and `many` unquoted, so not even JSON. Do
        not read this branch as an equivalent of the one above.
      * DeepSeek also refuses a **forced** `tool_choice` while thinking is on
        (`Thinking mode does not support this tool_choice`), so thinking is disabled to
        force the call. Unforced, the model may answer in prose and return no arguments
        at all.

    **So why tools rather than `json_object`, if neither constrains?** Because the tool
    definition *transmits* the schema and `json_object` transmits nothing — field names,
    types and nesting would have to be serialised into the prompt by hand, which is a
    second copy of the schema to keep in step with the pydantic model. Tools carry the
    real one. The guarantee is different from the transmission, and only the second is
    on offer here.

    **What actually holds the line on DeepSeek is `complete_json`'s client-side
    validation and its one re-ask.** On OpenAI that loop is a backstop; here it is the
    guardrail, and it should be read that way before anyone economises on it. §3.3's
    fabrication validator is downstream of both and is unaffected — it re-derives every
    claim from the vault regardless of how the JSON arrived.

    Branching on the base URL matches `model_slug_mismatch`, which is the other place a
    provider's spelling leaks in. A capability flag would be a second setting to keep in
    sync with the one that already says which provider this is.
    """
    if "deepseek" not in get_settings().llm_base_url:
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
        }
    return {
        "thinking": {"type": "disabled"},
        "tools": [
            {"type": "function", "function": {"name": name, "strict": True, "parameters": schema}}
        ],
        "tool_choice": {"type": "function", "function": {"name": name}},
    }


def _strict(node: Any) -> Any:
    """Rewrite a pydantic JSON Schema into one strict mode will accept.

    Three differences, all of them things pydantic is right about and strict mode is
    not:

      * every object must set `additionalProperties: false`;
      * every property must appear in `required` — optionality is expressed by the
        type being nullable, which ours already are (`str | None`), so listing them
        all is exactly correct rather than a lie;
      * `default` is not an allowed keyword, and pydantic emits one per defaulted
        field.

    Returns new dicts rather than editing in place: `model_json_schema()` results are
    cached per model class, and mutating one would corrupt every later call.
    """
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node

    rewritten = {key: _strict(value) for key, value in node.items() if key != "default"}
    if "properties" in rewritten:
        # Insertion order, not sorted: it is the field order the model reads, and a
        # résumé's sections have a natural sequence.
        rewritten["required"] = list(rewritten["properties"])
        rewritten["additionalProperties"] = False
    return rewritten
