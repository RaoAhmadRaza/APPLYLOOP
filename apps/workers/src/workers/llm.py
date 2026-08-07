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
"""

from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from workers.settings import Settings, get_settings

# One corrective re-ask, never a loop. If a schema-constrained model cannot satisfy its
# own schema twice, the prompt is wrong and burning credit will not fix it. Celery's
# retries sit above this and handle transport failures.
MAX_ATTEMPTS = 2

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


def is_configured() -> bool:
    """The interlock. No key means the caller no-ops and says so, never guesses."""
    return get_settings().llm_api_key is not None


def complete_json[T: BaseModel](schema: type[T], *, system: str, user: str) -> T:
    """One structured completion, validated into `schema`.

    Raises `LlmError` if the model fails the schema twice.
    """
    settings = get_settings()
    if settings.llm_api_key is None:
        raise LlmError("no LLM API key configured")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    body_schema = _strict(schema.model_json_schema())

    with _client(settings) as client:
        for attempt in range(MAX_ATTEMPTS):
            content = _ask(client, settings.llm_model, messages, schema.__name__, body_schema)
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


def _client(settings: Settings) -> httpx.Client:
    assert settings.llm_api_key is not None  # noqa: S101 — the caller checked
    return httpx.Client(
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        headers={
            "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "HTTP-Referer": _REFERER,
            "X-Title": _TITLE,
        },
        # Part 13 rule 12: the proxy is the aggregator's, and an ambient HTTPS_PROXY
        # would silently bill model traffic to residential bandwidth.
        trust_env=False,
    )


def _ask(
    client: httpx.Client,
    model: str,
    messages: list[dict[str, str]],
    name: str,
    schema: dict[str, Any],
) -> str:
    response = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        },
    )
    response.raise_for_status()
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LlmError(f"unexpected response shape: {sorted(payload)}") from error
    if not isinstance(content, str) or not content.strip():
        raise LlmError("model returned empty content")
    return content


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
