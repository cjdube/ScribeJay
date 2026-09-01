"""Tests for scribejay/core/usage_ledger.py — the append-only record of every
model call.

The path is redirected suite-wide by tests/conftest.py (both at import and per
test), so nothing here writes into the real logs/ directory."""

import json
import logging
from datetime import datetime, timedelta

import pytest

from scribejay.core import usage_ledger


def _rows():
    text = usage_ledger.LEDGER_PATH.read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# estimate_cost
# --------------------------------------------------------------------------- #

def test_local_calls_are_free_without_consulting_the_table():
    assert usage_ledger.estimate_cost("ollama", "gemma4:26b-mlx", 10_000, 2_000) == 0.0


def test_an_unknown_model_is_unpriced_not_free():
    """The one that matters: None means "we don't know", 0.0 would read as
    "this was free" and quietly hide a stale price table."""
    assert usage_ledger.estimate_cost("gemini", "gemini-9.9-nonesuch", 1000, 100) is None


def test_longest_matching_prefix_wins():
    # gemini-2.5-pro is (1.25, 10.00); gemini-2.5-flash is (0.30, 2.50).
    pro = usage_ledger.estimate_cost("gemini", "gemini-2.5-pro-preview", 1_000_000, 0)
    flash = usage_ledger.estimate_cost("gemini", "gemini-2.5-flash-preview-09-2025",
                                       1_000_000, 0)
    assert pro == 1.25
    assert flash == 0.30


def test_thinking_tokens_are_not_added_on_top():
    """Every provider counts them inside its output total already."""
    assert usage_ledger.estimate_cost("gemini", "gemini-2.5-flash", 0, 1_000_000) == 2.50


def test_missing_token_counts_are_treated_as_zero():
    assert usage_ledger.estimate_cost("gemini", "gemini-2.5-flash", None, None) == 0.0


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #

def test_records_one_row_with_the_agreed_shape():
    usage_ledger.record(
        "daily_chrome_learnings", "ollama", "gemma4:26b-mlx",
        prompt_tokens=123, output_tokens=45, num_ctx=8192,
        duration_ms=1500, finish_reason="stop", caller="complete_text",
    )

    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "scribejay"
    assert row["task"] == "daily_chrome_learnings"
    assert row["backend"] == "ollama"
    assert row["model"] == "gemma4:26b-mlx"
    assert row["prompt_tokens"] == 123
    assert row["output_tokens"] == 45
    assert row["num_ctx"] == 8192
    assert row["finish_reason"] == "stop"
    assert row["caller"] == "complete_text"
    assert row["tools_offered"] == 0
    assert row["ok"] is True
    assert row["error"] is None
    assert row["cost_usd"] == 0.0
    # Every field the outside reader depends on, named once.
    assert set(row) == {
        "ts", "agent", "task", "caller", "backend", "model", "prompt_tokens",
        "output_tokens", "thinking_tokens", "num_ctx", "duration_ms",
        "finish_reason", "tools_offered", "ok", "error", "cost_usd",
    }


def test_rows_append_rather_than_replace():
    usage_ledger.record("a", "ollama", "m")
    usage_ledger.record("b", "ollama", "m")
    assert [r["task"] for r in _rows()] == ["a", "b"]


def test_a_failed_call_still_produces_a_row():
    usage_ledger.record("t", "gemini", "gemini-2.5-flash", ok=False,
                        error="OllamaUnavailable: down")
    row = _rows()[0]
    assert row["ok"] is False
    assert row["error"] == "OllamaUnavailable: down"


def test_record_swallows_a_write_failure(monkeypatch, caplog):
    """Accounting is never worth a failed journal entry."""
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(usage_ledger, "locked", boom)
    with caplog.at_level(logging.DEBUG, logger=usage_ledger.logger.name):
        usage_ledger.record("t", "ollama", "m")  # must not raise
    assert "usage_ledger.record failed" in caplog.text


# --------------------------------------------------------------------------- #
# pruning
# --------------------------------------------------------------------------- #

def _write_rows(rows):
    usage_ledger.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage_ledger.LEDGER_PATH.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_no_prune_below_the_size_trigger(monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_USAGE_MAX_BYTES", "5000000")
    old = (datetime.now() - timedelta(days=999)).isoformat(timespec="seconds")
    _write_rows([{"ts": old, "task": "ancient"}])

    usage_ledger.record("fresh", "ollama", "m")

    assert [r["task"] for r in _rows()] == ["ancient", "fresh"]


def test_prune_drops_rows_past_the_retention_window(monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_USAGE_MAX_BYTES", "1")
    monkeypatch.setenv("SCRIBEJAY_USAGE_RETENTION_DAYS", "90")
    old = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    _write_rows([{"ts": old, "task": "ancient"}, {"ts": recent, "task": "recent"}])

    usage_ledger.record("fresh", "ollama", "m")

    assert [r["task"] for r in _rows()] == ["recent", "fresh"]


def test_a_row_of_unknown_age_is_kept(monkeypatch):
    """"I can't tell how old this is" is not evidence that it is old."""
    monkeypatch.setenv("SCRIBEJAY_USAGE_MAX_BYTES", "1")
    usage_ledger.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    old = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
    usage_ledger.LEDGER_PATH.write_text(
        "not json at all\n"
        + json.dumps({"task": "no-ts"}) + "\n"
        + json.dumps({"ts": old, "task": "ancient"}) + "\n",
        encoding="utf-8",
    )

    usage_ledger.record("fresh", "ollama", "m")

    text = usage_ledger.LEDGER_PATH.read_text()
    assert "not json at all" in text
    assert "no-ts" in text
    assert "ancient" not in text
