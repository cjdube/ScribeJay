"""Append-only record of every model call, one JSON object per line.

The token counts were always there — `core/model.py` has logged
`ollama_chat ... prompt_tokens=N eval_tokens=N` on every call for months, and
the two cloud backends log the same — but only as prose inside a log that a
RotatingFileHandler eventually throws away. This module keeps the same numbers
as data instead, so "how much is ScribeJay actually spending on models, and on
what?" is a question with an answer.

Nothing in this repo reads the file. Something outside it does, which is why
the field names below are a contract rather than a convenience: don't rename
one to read better.

One row per model call. The writer lives behind `core/model.py:_llm_chat`, the
single seam all three backends already pass through, so there is exactly one
call site and a fourth backend cannot be added and then quietly go unmeasured.

Two properties this file must keep:

  * It never raises. Accounting is not worth a failed journal entry, so
    `record()` swallows everything — a lost row is strictly better than a lost
    day's page.
  * It never grows without bound. It is written on every single model call, so
    it prunes itself on write (see `_prune_if_large`).

Deliberately `.jsonl`, not `.log`: `logs/*.log` in .gitignore would then cover
it by accident rather than on purpose, and a machine-readable ledger has no
business showing up beside the run logs as something a human is invited to
tail.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from scribejay.core import config
from scribejay.core.store import locked

# The one field that says which codebase a row came from. Literal, because the
# reader federates rows from more than one agent into a single ledger view.
AGENT = "scribejay"

LOGS_DIR = config.resolve_path(config.getenv("SCRIBEJAY_LOGS_DIR"))
LEDGER_PATH = LOGS_DIR / "usage.jsonl"

logger = logging.getLogger(__name__)


def _retention_days() -> int:
    try:
        return int(config.getenv("SCRIBEJAY_USAGE_RETENTION_DAYS"))
    except (TypeError, ValueError):
        return 90


def _max_bytes() -> int:
    try:
        return int(config.getenv("SCRIBEJAY_USAGE_MAX_BYTES"))
    except (TypeError, ValueError):
        return 5_000_000


# USD per MILLION tokens, as (input, output), keyed by model-name prefix so a
# pinned version ("gemini-2.5-flash-preview-09-2025") matches its family.
# Longest prefix wins.
#
# THESE RATES GO STALE. They are a hand-maintained convenience, not a billing
# record: check the provider's own pricing page before trusting a total, and
# the provider's console for what was actually charged. A model that matches
# nothing here records cost_usd=None and is counted separately by the reader —
# it must never be silently priced at zero, which would read as "free" rather
# than "unknown".
#
# OpenRouter's slugs are vendor-qualified ("anthropic/claude-...",
# "google/gemini-...") and need their own entries. Until one is added, an
# OpenRouter row comes through unpriced — which is correct, and visible.
#
# Local models are free at the point of use, so the ollama backend
# short-circuits to 0.0 without consulting this table at all.
_PRICES = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.7-flash": (0.30, 2.50),
}


def estimate_cost(backend: str, model: str, prompt_tokens, output_tokens) -> float | None:
    """USD for one call, or None when the model isn't in `_PRICES`.

    Thinking tokens are NOT added on top: every provider here already counts
    them inside its output-token total, so adding them again would double-bill
    exactly the calls that reason the most.
    """
    if (backend or "").lower() == "ollama":
        return 0.0
    name = (model or "").strip()
    match = None
    for prefix in _PRICES:
        if name.startswith(prefix) and (match is None or len(prefix) > len(match)):
            match = prefix
    if match is None:
        return None
    in_rate, out_rate = _PRICES[match]
    prompt = prompt_tokens if isinstance(prompt_tokens, int) else 0
    output = output_tokens if isinstance(output_tokens, int) else 0
    return round((prompt * in_rate + output * out_rate) / 1_000_000, 6)


def _prune_if_large(path: Path) -> None:
    """Drop rows older than the retention window once the file gets big.

    Size is the trigger and age is the rule, on purpose. Checking the size is
    one stat() on every call; rewriting a 5MB file is not, so a busy day pays
    the rewrite about once and a quiet one never pays it at all. Callers hold
    the lock.
    """
    try:
        if path.stat().st_size <= _max_bytes():
            return
    except OSError:
        return
    cutoff = (datetime.now() - timedelta(days=_retention_days())).isoformat()
    kept = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            # A row whose ts is missing or unreadable is kept: the point of the
            # prune is to shed old rows, and "I can't tell how old this is" is
            # not evidence that it is old.
            try:
                ts = json.loads(line).get("ts") or ""
            except ValueError:
                kept.append(line)
                continue
            if not ts or ts >= cutoff:
                kept.append(line)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    tmp.replace(path)


def record(
    task: str,
    backend: str,
    model: str,
    *,
    prompt_tokens=None,
    output_tokens=None,
    thinking_tokens=None,
    num_ctx=None,
    duration_ms=None,
    finish_reason=None,
    caller=None,
    tools_offered=0,
    ok: bool = True,
    error=None,
) -> None:
    """Append one row for one model call. Never raises.

    `tools_offered` is always 0 here — complete_text is tool-free by design —
    and the field is kept anyway so the row shape matches the other agents
    writing into the same reader.

    The timestamp is naive local time, matching what `logging` writes into the
    run logs beside it. That is deliberate — both this file and its reader are
    local-only, so there is no UTC boundary to cross and converting would
    introduce the very skew docs/timezones.md exists to prevent.
    """
    try:
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "agent": AGENT,
            "task": task,
            "caller": caller,
            "backend": backend,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "num_ctx": num_ctx,
            "duration_ms": duration_ms,
            "finish_reason": finish_reason,
            "tools_offered": tools_offered,
            "ok": ok,
            "error": error,
            "cost_usd": estimate_cost(backend, model, prompt_tokens, output_tokens),
        }
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Several launchd jobs can run at once and they write this same file,
        # so the append is serialized across processes, not just threads.
        with locked(LEDGER_PATH):
            with LEDGER_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            _prune_if_large(LEDGER_PATH)
    except Exception:
        # Debug, not warning: this fires on every call if it fires at all, and
        # a broken ledger must not drown the log the actual work writes to.
        logger.debug("usage_ledger.record failed", exc_info=True)
