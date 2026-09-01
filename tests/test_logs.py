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


# ---- the launchd log, which nothing rotated ---------------------------------

def _launchd_log(name: str, size: int):
    """A <task>.launchd.log of `size` bytes, in whole numbered lines so a test
    can tell which end survived."""
    path = _logs.LOGS_DIR / f"{name}.launchd.log"
    line = 0
    with open(path, "w") as f:
        written = 0
        while written < size:
            written += f.write(f"line {line:09d}\n")
            line += 1
    return path, line


def test_setup_logger_trims_a_launchd_log_that_grew_too_big():
    """launchd writes <task>.launchd.log and nothing rotated it — eight jobs
    appending every morning for the life of an install."""
    path, lines = _launchd_log("trim_task", _logs._LAUNCHD_LOG_MAX_BYTES + 50_000)
    before = path.stat().st_size

    _logs.setup_logger("trim_task")

    after = path.stat().st_size
    assert after < before
    assert after <= _logs._LAUNCHD_LOG_KEEP_BYTES + 100
    text = path.read_text()
    # The TAIL is what survives: this file holds what launchd said before the
    # logger existed, so the newest lines are the ones that explain a failure.
    assert f"line {lines - 1:09d}" in text
    assert "line 000000000" not in text
    assert text.startswith("[earlier output trimmed by ScribeJay]")


def test_setup_logger_leaves_a_small_launchd_log_alone():
    path, _ = _launchd_log("small_task", 1_000)
    before = path.read_text()

    _logs.setup_logger("small_task")

    assert path.read_text() == before


def test_setup_logger_does_not_mind_a_missing_launchd_log():
    """A task run by hand has never had one. Housekeeping on a log must never
    be the reason a run does not happen."""
    assert not (_logs.LOGS_DIR / "nolaunchd_task.launchd.log").exists()
    assert _logs.setup_logger("nolaunchd_task") is not None


def test_the_trim_never_splits_a_line():
    """Seeking by bytes lands mid-line, and half a timestamp reads as
    corruption to anyone tailing this file."""
    path, _ = _launchd_log("clean_task", _logs._LAUNCHD_LOG_MAX_BYTES + 50_000)

    _logs.setup_logger("clean_task")

    body = path.read_text().splitlines()[1:]
    assert all(line.startswith("line ") and len(line) == 14 for line in body)
