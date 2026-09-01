"""Tests for scribejay/sinks/vault.py — write_entry and the
persist_or_email write-fails -> email fallback contract. Collaborators are
monkeypatched; nothing touches the real vault or Gmail.

Mirrors the persist_or_email slice of LocalLLMAgent's tests/test_activity_log.py
(write_entry itself, near-identical to agent/tools/learnings_file.py, is
exercised only indirectly here through persist_or_email)."""

import logging
from datetime import date

import pytest

from scribejay.sinks import vault as lc

_LOG = logging.getLogger("test_vault")


def test_write_entry_writes_the_file(tmp_path):
    result = lc.write_entry("body text", "Daily-Chrome", date(2026, 7, 12), directory=tmp_path)
    assert result["written"] is True
    written = tmp_path / "Daily-Chrome-2026-07-12.md"
    assert written.read_text() == "body text"


def test_write_entry_missing_dir_errors_without_creating_it(tmp_path):
    missing = tmp_path / "nope"
    result = lc.write_entry("body", "Daily-Chrome", date(2026, 7, 12), directory=missing)
    assert "error" in result
    assert not missing.exists()


@pytest.fixture
def spy(monkeypatch):
    seen = {"emails": [], "failures": [], "writes": []}
    monkeypatch.setattr(lc, "write_entry",
                        lambda content, prefix, day, directory=None: seen["writes"].append((prefix, content, directory)) or {"written": True})
    monkeypatch.setattr(lc, "send_email",
                        lambda subject, body: seen["emails"].append((subject, body)) or {"message_id": "m1"})
    monkeypatch.setattr(lc, "notify_failure",
                        lambda name, detail, logger=None: seen["failures"].append(str(detail)))
    return seen


def test_persist_writes_and_sends_no_email(spy):
    lc.persist_or_email("body", "Daily-Chrome", date(2026, 7, 12), "subj", "daily_chrome_learnings", _LOG)
    assert spy["writes"] and not spy["emails"] and spy["failures"] == []


def test_persist_write_failure_emails_the_draft(spy, monkeypatch):
    monkeypatch.setattr(lc, "write_entry", lambda content, prefix, day, directory=None: {"error": "target dir not found"})
    lc.persist_or_email("body", "Daily-Chrome", date(2026, 7, 12), "subj", "daily_chrome_learnings", _LOG)
    assert spy["emails"] == [("subj", "body")]
    assert any("vault write failed" in f for f in spy["failures"])


def test_persist_write_and_email_both_failing_raises(spy, monkeypatch):
    monkeypatch.setattr(lc, "write_entry", lambda content, prefix, day, directory=None: {"error": "target dir not found"})
    monkeypatch.setattr(lc, "send_email", lambda subject, body: {"error": "gmail 503"})
    with pytest.raises(RuntimeError, match="both failed"):
        lc.persist_or_email("body", "Daily-Chrome", date(2026, 7, 12), "subj", "daily_chrome_learnings", _LOG)


def test_learnings_dir_resolves_a_relative_setting_under_the_config_dir(monkeypatch, tmp_path):
    """LEARNINGS_DIR is a type="path" row, so a relative value means "beside the
    checkout, or under ~/.scribejay" — never "relative to whatever directory
    launchd happened to start the job in", which is what Path(value) gave.

    This is the pairing that made it matter: `doctor` probes the same setting
    through config.resolve_path, so before the fix the health check and the
    writer could report on two different folders.
    """
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("LEARNINGS_DIR", "vault-under-config")

    assert lc._learnings_dir() == tmp_path / "vault-under-config"
