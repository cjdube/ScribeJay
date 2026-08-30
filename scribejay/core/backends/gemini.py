"""Gemini cloud backend behind core.model's _llm_chat seam.

Translates ScribeJay's canonical (Ollama/OpenAI-shaped) messages and tool
schemas to/from the Gemini SDK, and reassembles a streamed reply into the
same canonical `message` dict the Ollama path returns. Opt-in per the
local-first design (SCRIBEJAY_LLM_BACKEND / SCRIBEJAY_<TASK>_BACKEND=gemini).

Mirrors LocalLLMAgent's agent/backends/gemini.py, minus the tool-calling
plumbing (functionCall/functionResponse translation, thought_signature
carry-through, should_cancel/TurnCancelled) — ScribeJay never calls
_gemini_chat with tools and has no interactive turn to cancel; every call is
one system/user turn from complete_text().

The google.genai imports stay inside the functions so importing this module
never pulls the cloud SDK on the common local-only path."""

import logging
from typing import Optional

from scribejay.core import config
from scribejay.core.http import resolve_key

# Default cloud model when the Gemini backend is selected but no model is pinned.
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


def _gemini_contents(messages: list[dict]):
    """Translate canonical system/user messages into (system_instruction,
    contents) for the Gemini SDK. No tool_calls/tool results here — ScribeJay's
    complete_text() only ever sends one system + one user turn."""
    from google.genai import types

    system_parts: list[str] = []
    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            contents.append(types.Content(
                role="user", parts=[types.Part.from_text(text=m.get("content") or "")]))
    system = "\n\n".join(system_parts) if system_parts else None
    return system, contents


def _gemini_client(timeout: float = None):
    """Build a Gemini client. The key comes from GEMINI_API_KEY or
    GOOGLE_API_KEY (the SDK's own fallback order). Isolated so tests can stub
    it."""
    from google import genai
    from google.genai import types

    api_key = resolve_key("GEMINI_API_KEY") or resolve_key("GOOGLE_API_KEY")
    kwargs = {"api_key": api_key}
    if timeout is not None:
        kwargs["http_options"] = types.HttpOptions(timeout=int(timeout * 1000))  # ms
    return genai.Client(**kwargs)


def _gemini_chat(
    messages: list[dict],
    model: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    think: Optional[bool] = None,
) -> dict:
    """Gemini backend for a single system+user completion. Streams,
    reassembling the reply into the same canonical `message` dict the Ollama
    path returns."""
    from google.genai import types

    model = model or config.getenv("SCRIBEJAY_GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    max_out = int(config.getenv("SCRIBEJAY_GEMINI_MAX_OUTPUT_TOKENS"))
    # Gemini models are *thinking* models, and thinking tokens count against
    # max_output_tokens. See LocalLLMAgent's agent/backends/gemini.py for the
    # measured per-model quirks this budget guards against; the setting should
    # be pinned the same way here (128 is portable).
    thinking_budget = int(config.getenv("SCRIBEJAY_GEMINI_THINKING_BUDGET"))
    _ = think  # not honoured here — see thinking_budget above.
    system, contents = _gemini_contents(messages)

    cfg_kwargs = dict(
        max_output_tokens=max_out,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    if system:
        cfg_kwargs["system_instruction"] = system
    gen_config = types.GenerateContentConfig(**cfg_kwargs)

    client = _gemini_client(timeout=timeout)
    content_parts: list[str] = []
    prompt_tokens = output_tokens = thinking_tokens = None
    finish_reason = None
    stream = client.models.generate_content_stream(model=model, contents=contents, config=gen_config)
    for chunk in stream:
        cand = (chunk.candidates or [None])[0]
        if cand and cand.content and cand.content.parts:
            for p in cand.content.parts:
                if getattr(p, "text", None):
                    content_parts.append(p.text)
        if cand and getattr(cand, "finish_reason", None):
            finish_reason = cand.finish_reason
        um = getattr(chunk, "usage_metadata", None)
        if um:
            prompt_tokens = getattr(um, "prompt_token_count", None) or prompt_tokens
            output_tokens = getattr(um, "candidates_token_count", None) or output_tokens
            thinking_tokens = getattr(um, "thoughts_token_count", None) or thinking_tokens

    message: dict = {"role": "assistant", "content": "".join(content_parts)}
    if logger:
        reason = str(finish_reason) if finish_reason is not None else None
        logger.info(
            "gemini_chat model=%s prompt_tokens=%s output_tokens=%s "
            "thinking_tokens=%s finish_reason=%s",
            model, prompt_tokens, output_tokens, thinking_tokens, reason,
        )
        if reason and "MAX_TOKENS" in reason:
            logger.warning(
                "gemini generation hit finish_reason=MAX_TOKENS and was cut off "
                "(output_tokens=%s, thinking_tokens=%s) — the draft is likely "
                "incomplete; raise SCRIBEJAY_GEMINI_MAX_OUTPUT_TOKENS or lower "
                "SCRIBEJAY_GEMINI_THINKING_BUDGET",
                output_tokens, thinking_tokens,
            )
    return message
