"""Single-turn model calls for ScribeJay's pipeline tasks.

ScribeJay is gather -> one complete_text() call -> write. There is no tool
registry and no tool-calling loop here — no confirmation gates, no cancel
path, no iteration cap — because there is no interactive turn to protect.

**Identity is deliberately thin.** with_identity() composes exactly two
things: scribejay/persona.md and the caller's system_prompt. A journaling task
has no business reading a personal identity file or a memory store, so none is
wired in.

Backend selection is SCRIBEJAY_<TASK>_BACKEND -> SCRIBEJAY_LLM_BACKEND ->
ollama — see backend()/log_backend() below.

Usage:
    from scribejay.core.model import backend, complete_text, log_backend, warm_model
    b = backend("daily_chrome_learnings")
    log_backend(logger, "daily_chrome_learnings", b)
    warm_model(backend=b, logger=logger)
    text = complete_text(system_prompt, user_prompt, backend=b, logger=logger)
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

from scribejay.core import config
from scribejay.core.backends.gemini import GEMINI_DEFAULT_MODEL, _gemini_chat
from scribejay.core.backends.openrouter import _openrouter_chat


def backend(task_key: str) -> str | None:
    """SCRIBEJAY_<TASK_KEY>_BACKEND, else SCRIBEJAY_LLM_BACKEND, else None.

    `None` means "no opinion", which _llm_chat resolves to local Ollama — the
    local-first default. Deliberately no fallback to a legacy variable name
    from the codebase this split out of: a silent fallback would hide a missed
    setup, where resolving to
    ollama and SAYING SO in the run log (log_backend below) is the louder,
    correct failure."""
    return (
        config.getenv(f"SCRIBEJAY_{task_key.upper()}_BACKEND")
        or config.getenv("SCRIBEJAY_LLM_BACKEND")
        or None
    )


def log_backend(logger, task_key: str, resolved: str | None) -> None:
    """Say which backend the run resolved to, and where it came from.

    Logged on every run because the failure this guards against is silent: an
    unset variable is not an error, it is just a different (smaller) model,
    and the only visible symptom is a thinner draft nobody compares against
    yesterday's."""
    if config.getenv(f"SCRIBEJAY_{task_key.upper()}_BACKEND"):
        source = f"SCRIBEJAY_{task_key.upper()}_BACKEND"
    elif config.getenv("SCRIBEJAY_LLM_BACKEND"):
        source = "SCRIBEJAY_LLM_BACKEND"
    else:
        source = "unset"
    logger.info(f"backend: {resolved or 'ollama (default)'} (from {source})")


def _resolve_backend(backend_arg: Optional[str] = None) -> str:
    """explicit arg -> SCRIBEJAY_LLM_BACKEND env -> 'ollama' fallback.

    In practice every ScribeJay task resolves its backend via backend() above
    and passes it explicitly, so this env fallback is a safety net rather
    than the primary path."""
    return (backend_arg or config.getenv("SCRIBEJAY_LLM_BACKEND") or "ollama").strip().lower()


def load_persona() -> str:
    """ScribeJay's own persona file, stripping HTML comments (those are notes
    for maintainers, not the model)."""
    try:
        raw = (Path(__file__).resolve().parent.parent / "persona.md").read_text()
        return re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
    except FileNotFoundError:
        return ""


SCRIBEJAY_PERSONA = load_persona()


def with_identity(system_prompt: str) -> str:
    """ScribeJay's persona plus the caller's system prompt. Nothing else —
    see the module docstring for why no identity file or memory store is
    carried in."""
    parts = [p for p in (SCRIBEJAY_PERSONA, system_prompt) if p]
    return "\n\n---\n\n".join(parts)


class OllamaUnavailable(Exception):
    """Raised when a model call produced nothing and we have classified why."""


def _diagnose_stall(host: str, got_bytes: bool, waited: float) -> str:
    """Explain a model call that timed out without finishing — down, busy
    (another job holding Ollama's one request slot), or a wedged runner.
    Probing /api/ps afterwards separates "down" from the other two."""
    waited_s = f"{waited:.0f}s"
    stalled = "mid-reply" if got_bytes else "without producing any output"
    try:
        resp = requests.get(f"{host}/api/ps", timeout=5)
        resp.raise_for_status()
        loaded = [m.get("name", "?") for m in (resp.json().get("models") or [])]
    except Exception:
        return (f"Ollama at {host} did not respond within {waited_s} and is not "
                f"answering status checks either — it looks down. Check that it "
                f"is running.")
    if not loaded:
        return (f"Ollama at {host} is up but has no model loaded and stalled "
                f"{stalled} after {waited_s}.")
    return (f"Ollama at {host} is up (model {', '.join(loaded)} loaded) but "
            f"stalled {stalled} after {waited_s}. It serves one request at a "
            f"time, so it is either busy with another job or its runner is "
            f"wedged. Retry; if it repeats, restart Ollama.")


# A reasoning model's scratchpad is meant to arrive in its own `thinking`
# field, which is discarded. Some models leak the terminator into `content`
# instead — a complete answer followed by a bare `</think>`. Strip the
# markup, never the answer: a matched block is scratchpad and goes whole, an
# orphan tag is a leak and only the tag goes.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think>")


def _strip_think_markup(text: str) -> str:
    if not text or "think>" not in text:
        return text
    return _THINK_TAG_RE.sub("", _THINK_BLOCK_RE.sub("", text)).strip()


# gemma4:26b-mlx writes `$\rightarrow$` where it means →, and a Markdown vault
# page does not render math — the raw markup lands in the written record.
#
# The backslash is what makes this safe. A span converts only as `$\command$`,
# never as `$...$`: dollar signs here are overwhelmingly money, and a rule
# spanning two of them would eat the prose in between. Money carries no
# backslash.
#
# Only \rightarrow was measured; the rest are its immediate neighbours, a row
# of data each rather than new code. Deliberately narrow — `$n=2$` has no
# backslash and is left alone, as is any command not on this list.
_LATEX_SYMBOLS = {
    "rightarrow": "→", "to": "→",
    "leftarrow": "←", "gets": "←",
    "leftrightarrow": "↔",
    "Rightarrow": "⇒",
    "le": "≤", "leq": "≤",
    "ge": "≥", "geq": "≥",
    "ne": "≠", "neq": "≠",
    "approx": "≈",
    "times": "×",
    "pm": "±",
}
# Longest name first so `\leq` is never tried as `\le`, and a trailing-letter
# check so an ordinary word like `\total` doesn't come back as `→tal`.
_LATEX_NAMES = "|".join(sorted(_LATEX_SYMBOLS, key=len, reverse=True))
_LATEX_RE = re.compile(
    rf"\$\\({_LATEX_NAMES})\$"           # $\rightarrow$ — the form the model writes
    rf"|\\({_LATEX_NAMES})(?![A-Za-z])"  # a bare \rightarrow
)


def _latex_to_unicode(text: str) -> str:
    # Untouched when there's no backslash at all, so the common clean reply
    # keeps its exact whitespace.
    if not text or "\\" not in text:
        return text
    return _LATEX_RE.sub(lambda m: _LATEX_SYMBOLS[m.group(1) or m.group(2)], text)


def _ollama_chat(
    messages: list[dict],
    model: str = None,
    host: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    think: Optional[bool] = None,
) -> dict:
    """POST a single chat completion to Ollama and return the reassembled
    response `message` dict.

    Streams so a stall can be diagnosed from what arrived (or didn't); there
    is no cancel path here (ScribeJay's tasks are unattended, nothing to
    cancel), so the stream just runs to completion or times out."""
    model = model or config.getenv("OLLAMA_MODEL")
    host = host or config.getenv("OLLAMA_HOST")
    if timeout is None:
        timeout = float(config.getenv("OLLAMA_TIMEOUT"))
    num_ctx = int(config.getenv("OLLAMA_NUM_CTX"))
    num_predict = int(config.getenv("OLLAMA_NUM_PREDICT"))

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": config.getenv("OLLAMA_KEEP_ALIVE"),
        "options": {"num_ctx": num_ctx, "num_predict": num_predict},
    }
    if think is not None:
        payload["think"] = think

    content_parts: list[str] = []
    prompt_tokens = eval_tokens = None
    got_bytes = False
    t0 = time.monotonic()
    try:
        with requests.post(f"{host}/api/chat", json=payload, timeout=timeout,
                           stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                got_bytes = True
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message") or {}
                if msg.get("content"):
                    content_parts.append(msg["content"])
                if chunk.get("done"):
                    prompt_tokens = chunk.get("prompt_eval_count")
                    eval_tokens = chunk.get("eval_count")
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        detail = _diagnose_stall(host, got_bytes, time.monotonic() - t0)
        if logger:
            logger.warning("ollama_chat stalled: %s", detail)
        raise OllamaUnavailable(detail) from e

    message: dict = {"role": "assistant", "content": "".join(content_parts)}
    if logger:
        logger.info(
            "ollama_chat model=%s num_ctx=%d prompt_tokens=%s eval_tokens=%s",
            model, num_ctx, prompt_tokens, eval_tokens,
        )
        if isinstance(prompt_tokens, int) and prompt_tokens >= num_ctx:
            logger.warning(
                "ollama prompt (%d tokens) reached num_ctx=%d — the front of "
                "the conversation (system prompt) was likely truncated",
                prompt_tokens, num_ctx,
            )
        if isinstance(eval_tokens, int) and eval_tokens >= num_predict:
            logger.warning(
                "ollama generation (%d tokens) reached num_predict=%d and was "
                "cut off — healthy replies stay well under the cap, so this "
                "means either a repetition loop or a thinking model spending "
                "the whole budget on scratchpad (which returns EMPTY content; "
                "pass think=False for template-filling calls)",
                eval_tokens, num_predict,
            )
    return message


def warm_model(
    model: str = None,
    host: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    backend: Optional[str] = None,
) -> bool:
    """Force-load the model into Ollama's memory before a heavy generation.

    A no-op (returns True) for any non-Ollama backend. A cold local model
    can't emit its first streamed chunk until it's loaded AND the prompt is
    prefilled; on a cold start these can exceed the streamed call's read
    timeout. Loading the model first — with the SAME num_ctx and keep_alive,
    so the real call reuses this resident instance — moves that cost out of
    the timeout window. Degrades to a warning and returns False on failure;
    the caller still attempts the generation cold."""
    if _resolve_backend(backend) != "ollama":
        return True
    model = model or config.getenv("OLLAMA_MODEL")
    host = host or config.getenv("OLLAMA_HOST")
    if timeout is None:
        timeout = float(config.getenv("OLLAMA_WARM_TIMEOUT"))
    num_ctx = int(config.getenv("OLLAMA_NUM_CTX"))
    payload = {
        "model": model,
        "messages": [],
        "stream": False,
        "keep_alive": config.getenv("OLLAMA_KEEP_ALIVE"),
        "options": {"num_ctx": num_ctx},
    }
    try:
        t0 = time.monotonic()
        resp = requests.post(f"{host}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        if logger:
            logger.info("warm_model loaded %s in %.1fs", model, time.monotonic() - t0)
        return True
    except Exception as e:
        if logger:
            logger.warning("warm_model failed (%s); attempting generation cold", e)
        return False


def _llm_chat(
    messages: list[dict],
    backend: Optional[str] = None,
    model: str = None,
    host: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    think: Optional[bool] = None,
) -> dict:
    """Dispatch a single-turn chat completion to the selected backend,
    returning the same canonical `message` dict shape regardless of
    provider."""
    b = _resolve_backend(backend)
    if b == "ollama":
        message = _ollama_chat(messages, model=model, host=host, timeout=timeout,
                               logger=logger, think=think)
    elif b in ("gemini", "google"):
        message = _gemini_chat(messages, model=model, timeout=timeout,
                               logger=logger, think=think)
    elif b == "openrouter":
        message = _openrouter_chat(messages, model=model, timeout=timeout,
                                   logger=logger, think=think)
    else:
        raise ValueError(
            f"unknown SCRIBEJAY_LLM_BACKEND {b!r} "
            f"(expected 'ollama', 'gemini' or 'openrouter')")
    content = _strip_think_markup(message.get("content") or "")
    message["content"] = _latex_to_unicode(content)
    return message


def complete_text(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    host: str = None,
    timeout: float = None,
    logger: Optional[logging.Logger] = None,
    backend: Optional[str] = None,
    think: Optional[bool] = None,
) -> str:
    """Single-turn, tool-free completion — every ScribeJay task's one model
    call. The caller assembles the surrounding structure (Markdown headers,
    counts, dates) itself; the model only writes the blurb.

    Pass `think=False` for a call that fills in a template — a classification,
    a score, a fixed output format. Thinking tokens are drawn from the same
    num_predict budget as the answer, so a model that reasons too long returns
    EMPTY content rather than a truncated answer (docs/model-constraints.md)."""
    message = _llm_chat(
        [
            {"role": "system", "content": with_identity(system_prompt)},
            {"role": "user", "content": user_prompt},
        ],
        backend=backend,
        model=model,
        host=host,
        timeout=timeout,
        logger=logger,
        think=think,
    )
    return message.get("content", "").strip()
