"""OpenRouter cloud backend behind core.model's _llm_chat seam.

OpenRouter fronts every frontier model behind one OpenAI-compatible
`/chat/completions` endpoint and one API key, so this single backend covers
"use my own key with a frontier model" without a separate Anthropic and
OpenAI backend each. Opt-in per the local-first design
(SCRIBEJAY_LLM_BACKEND / SCRIBEJAY_<TASK>_BACKEND=openrouter).

Unlike the Ollama and Gemini paths this does not stream. Streaming exists
there to diagnose a stalled local runner and to reassemble the Gemini SDK's
chunks; a single hosted HTTP call has neither problem, and a plain POST with
an explicit timeout is the whole story. ScribeJay's canonical message shape
is already the OpenAI shape, so there is no translation layer either.

`requests` is the only dependency, already required — a purely local install
gains nothing to import.
"""

import logging
from typing import Optional

import requests

from scribejay.core import config
from scribejay.core.http import resolve_key

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default hosted model when the OpenRouter backend is selected but no model is
# pinned. A capable frontier model rather than a cheap one: someone who picked
# this backend picked it to escape the small local model.
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-sonnet-5"

# Every HTTP call gets an explicit timeout. Not a setting: unlike the Ollama
# timeout, which guards a machine that may be loading a 20GB model off disk,
# this one only guards an unattended launchd job against a hung socket. The
# caller can still pass its own.
OPENROUTER_TIMEOUT_S = 300.0


class OpenRouterError(Exception):
    """An OpenRouter call that produced no usable reply, with the reason."""


def _post(payload: dict, timeout: float) -> dict:
    """The one network call, isolated so tests can stub it.

    Reads the body on an error status rather than using raise_for_status():
    OpenRouter puts the actual reason (bad model slug, no credit, rate limit)
    in the JSON body, and a nightly log that says only "HTTP 402" costs a
    debugging session."""
    api_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError(
            "OPENROUTER_API_KEY not set (checked env var, config/.env, Keychain)")
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise OpenRouterError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _openrouter_chat(
    messages: list[dict],
    model: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    think: Optional[bool] = None,
) -> dict:
    """OpenRouter backend for a single system+user completion, returning the
    same canonical `message` dict the Ollama path returns."""
    model = model or config.getenv("OPENROUTER_MODEL")
    max_out = int(config.getenv("OPENROUTER_MAX_OUTPUT_TOKENS"))

    payload: dict = {
        "model": model,
        "messages": [{"role": m.get("role"), "content": m.get("content") or ""}
                     for m in messages],
        "max_tokens": max_out,
    }
    # Reasoning tokens are billed and, on some models, counted against
    # max_tokens — the same trap docs/model-constraints.md describes for the
    # local models. A caller that passed think=False wants a filled template,
    # so say so; leave the model's own default alone otherwise.
    if think is False:
        payload["reasoning"] = {"enabled": False}

    data = _post(payload, OPENROUTER_TIMEOUT_S if timeout is None else timeout)

    # OpenRouter can answer 200 with an error object instead of choices.
    if data.get("error"):
        raise OpenRouterError(f"OpenRouter returned an error: {data['error']}")

    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    finish_reason = choice.get("finish_reason")
    usage = data.get("usage") or {}

    message: dict = {"role": "assistant", "content": content}
    if logger:
        # `served` is what OpenRouter actually routed to, which is not always
        # the slug asked for (a `:floor`/auto route, or a fallback). The
        # failure mode this whole backend guards against is a silently
        # different model, so log both.
        logger.info(
            "openrouter_chat model=%s served=%s prompt_tokens=%s output_tokens=%s "
            "finish_reason=%s",
            model, data.get("model"), usage.get("prompt_tokens"),
            usage.get("completion_tokens"), finish_reason,
        )
        if finish_reason == "length":
            logger.warning(
                "openrouter generation hit finish_reason=length and was cut off "
                "(output_tokens=%s) — the draft is likely incomplete; raise "
                "OPENROUTER_MAX_OUTPUT_TOKENS",
                usage.get("completion_tokens"),
            )
        elif not content:
            logger.warning(
                "openrouter returned EMPTY content (model=%s finish_reason=%s) — "
                "a reasoning model can spend the whole max_tokens budget on "
                "reasoning; pass think=False for template-filling calls",
                model, finish_reason,
            )
    return message
