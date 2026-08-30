"""Tests for scribejay/core/secrets.py — credentials in the macOS Keychain.

conftest.py stubs `_run` suite-wide so no test can touch the developer's real
login Keychain. Every test here re-patches that stub to drive the real code,
which is the same pattern test_notify.py and test_clickup.py use for their
egress guards.

The behaviour that matters most is degradation: a missing `security` binary, a
locked keychain, or an absent item must all read as "no credential" rather than
raising, because these calls happen inside unattended 4 AM runs where a raised
exception is a failed job and a `None` is a clean `missing_key_error`.
"""

import logging
import subprocess

import pytest

from scribejay.core import secrets

# Captured at import — that is, at collection time, BEFORE conftest's autouse
# _block_keychain fixture swaps _run out. The three tests that exercise the
# real _run (its subprocess error handling) have to put this back, or they
# drive conftest's stub instead and pass without touching the code they name.
_REAL_RUN = secrets._run


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(monkeypatch, handler):
    """Replace the one subprocess choke point, recording what it was asked."""
    calls = []

    def _run(args, stdin=None):
        calls.append(args)
        return handler(args)

    monkeypatch.setattr(secrets, "_run", _run)
    return calls


# ---- reading ----------------------------------------------------------------

def test_get_returns_the_stored_value(monkeypatch):
    # `security -w` prints the password followed by a newline.
    _fake_run(monkeypatch, lambda args: _Result(stdout="s3cret\n"))
    assert secrets.get("STRAVA_CLIENT_SECRET") == "s3cret"


def test_get_asks_for_the_right_item(monkeypatch):
    calls = _fake_run(monkeypatch, lambda args: _Result(stdout="x\n"))
    secrets.get("CLICKUP_API_TOKEN")
    assert calls[0][0] == "find-generic-password"
    assert "-s" in calls[0] and secrets.SERVICE in calls[0]
    assert "-a" in calls[0] and "CLICKUP_API_TOKEN" in calls[0]


def test_missing_item_is_none_not_an_error(monkeypatch):
    # 44 is errSecItemNotFound — the ordinary "you never set this" answer.
    _fake_run(monkeypatch, lambda args: _Result(returncode=44, stderr="not found"))
    assert secrets.get("NEVER_SET") is None


def test_other_failures_also_degrade_to_none(monkeypatch):
    # A locked keychain, a denied prompt, a broken install: all the same answer
    # to a 4 AM task, which then reports missing_key_error and exits cleanly.
    _fake_run(monkeypatch, lambda args: _Result(returncode=1, stderr="User interaction is not allowed."))
    assert secrets.get("STRAVA_REFRESH_TOKEN") is None


def test_unavailable_security_binary_is_none(monkeypatch):
    # _run returns None when the command could not be run at all — a Linux CI
    # box, or a Mac without the command line tools.
    monkeypatch.setattr(secrets, "_run", lambda args, stdin=None: None)
    assert secrets.get("ANY") is None
    assert secrets.set("ANY", "v") is False
    assert secrets.delete("ANY") is False


def test_empty_stored_value_reads_as_unset(monkeypatch):
    # An item holding "" is indistinguishable from no item for every caller,
    # and resolve_key()'s `or` chain would skip it anyway.
    _fake_run(monkeypatch, lambda args: _Result(stdout="\n"))
    assert secrets.get("BLANK") is None


def test_a_real_subprocess_failure_degrades(monkeypatch):
    monkeypatch.setattr(secrets, "_run", _REAL_RUN)

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=10)
    monkeypatch.setattr(secrets.subprocess, "run", _boom)
    assert secrets.get("ANY") is None


def test_missing_binary_degrades(monkeypatch):
    monkeypatch.setattr(secrets, "_run", _REAL_RUN)

    def _boom(*a, **k):
        raise FileNotFoundError("/usr/bin/security")
    monkeypatch.setattr(secrets.subprocess, "run", _boom)
    assert secrets.get("ANY") is None


# ---- writing ----------------------------------------------------------------

def test_set_updates_in_place(monkeypatch):
    # -U, so re-pasting a rotated token replaces the item rather than failing
    # on a duplicate and leaving the old one live.
    calls = _fake_run(monkeypatch, lambda args: _Result())
    assert secrets.set("GEMINI_API_KEY", "abc") is True
    assert calls[0][0] == "add-generic-password"
    assert "-U" in calls[0]


def test_set_refuses_an_empty_value(monkeypatch):
    calls = _fake_run(monkeypatch, lambda args: _Result())
    assert secrets.set("GEMINI_API_KEY", "") is False
    assert not calls, "an empty secret must not reach the keychain at all"


def test_set_reports_failure(monkeypatch):
    _fake_run(monkeypatch, lambda args: _Result(returncode=1, stderr="denied"))
    assert secrets.set("GEMINI_API_KEY", "abc") is False


def test_delete_treats_missing_as_success(monkeypatch):
    _fake_run(monkeypatch, lambda args: _Result(returncode=44))
    assert secrets.delete("GONE") is True


def test_is_set_does_not_return_the_value(monkeypatch):
    _fake_run(monkeypatch, lambda args: _Result(stdout="s3cret\n"))
    assert secrets.is_set("STRAVA_CLIENT_SECRET") is True


# ---- the value must never be logged -----------------------------------------

def test_no_secret_value_reaches_the_logs(monkeypatch, caplog):
    """The settings screen and the migration both print what they did; the one
    place a value could leak is a log line from this module. Drive both the
    failure paths that log."""
    with caplog.at_level(logging.DEBUG, logger=secrets.logger.name):
        _fake_run(monkeypatch, lambda args: _Result(returncode=1, stderr="nope"))
        secrets.set("GEMINI_API_KEY", "SUPER-SECRET-VALUE")
        secrets.get("GEMINI_API_KEY")
    assert "SUPER-SECRET-VALUE" not in caplog.text


def test_a_subprocess_exception_does_not_log_its_argv(monkeypatch, caplog):
    # The argv of a write carries the secret, and an exception's str() can
    # carry the argv — so the handler logs the exception TYPE, not the text.
    monkeypatch.setattr(secrets, "_run", _REAL_RUN)

    def _boom(*a, **k):
        raise OSError("security add-generic-password -w SUPER-SECRET-VALUE failed")
    monkeypatch.setattr(secrets.subprocess, "run", _boom)
    with caplog.at_level(logging.DEBUG, logger=secrets.logger.name):
        secrets.set("GEMINI_API_KEY", "SUPER-SECRET-VALUE")
    assert "SUPER-SECRET-VALUE" not in caplog.text


def test_available_reports_the_binary(monkeypatch):
    monkeypatch.setattr(secrets.os.path, "exists", lambda p: False)
    assert secrets.available() is False
