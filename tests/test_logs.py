"""Tests for scribejay/core/logs.py — the shared task-runner helpers (logger
setup, best-effort failure push). Log output is redirected to a tmp dir by
conftest; the ntfy push is stubbed there too, and re-stubbed per-test here to
observe what notify_failure sends.

Mirrors the setup_logger + notify_failure slice of LocalLLMAgent's
tests/test_common.py — dropped today_str (not carried over) and the
startup-recovery deferral test, since ScribeJay has no startup_recovery
coordinator (scribejay/core/logs.py's own docstring: a failed run always
pushes its own alert immediately)."""

import logging

from scribejay.core import logs as _logs


def test_setup_logger_writes_to_the_redirected_dir():
    # conftest points _logs.LOGS_DIR at a tmp dir for every test; assert
    # setup_logger writes there (and never the real logs/, which conftest's
    # handler guard would otherwise reject).
    logger = _logs.setup_logger("unittest_task")
    logger.info("hello world")
    for handler in logger.handlers:
        handler.flush()
    log_file = _logs.LOGS_DIR / "unittest_task.log"
    assert log_file.exists()
    assert "hello world" in log_file.read_text()


def test_setup_logger_is_isolated_and_does_not_stack_handlers():
    logger = _logs.setup_logger("iso_task")
    assert logger.propagate is False
    # A second setup for the same name clears old handlers rather than doubling
    # them (file + stream), so re-running a task can't multiply its log lines.
    again = _logs.setup_logger("iso_task")
    assert len(again.handlers) == 2


def test_notify_failure_pushes_high_priority_with_email_fallback(monkeypatch):
    calls = {}

    def fake_notify(message, title=None, priority=None, email_fallback=False):
        calls.update(message=message, title=title, priority=priority,
                     email_fallback=email_fallback)
        return {"ok": True}
    monkeypatch.setattr(_logs, "notify", fake_notify)
    _logs.notify_failure("daily_commits", "boom")
    assert calls["email_fallback"] is True     # a one-shot alert must not be lost
    assert calls["priority"] == "high"
    assert "daily_commits" in calls["title"]
    assert "boom" in calls["message"]


def test_notify_failure_swallows_push_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("push exploded")
    monkeypatch.setattr(_logs, "notify", boom)
    # Must not raise — the task failure it's reporting is already logged.
    _logs.notify_failure("t", "detail", logger=logging.getLogger("test_logs"))


def test_notify_failure_logs_when_push_reports_an_error(monkeypatch):
    monkeypatch.setattr(_logs, "notify", lambda *a, **k: {"error": "ntfy down"})
    warnings = []

    class _Logger:
        def warning(self, msg, *args):
            warnings.append(msg % args if args else msg)

        def exception(self, *a, **k):
            pass

    _logs.notify_failure("t", "d", logger=_Logger())
    assert any("did not send" in w for w in warnings)
