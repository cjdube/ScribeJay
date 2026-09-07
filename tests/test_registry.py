"""Tests for scribejay/core/registry.py — a task that was switched off.

The whole point of Phase 3 is one morning: eight jobs fire, the ones whose
sources the user declined do nothing, and the user's phone stays silent. Three
things have to hold for that, and none of them fails loudly on its own:

- exit 0, so launchd does not retry and the self-heal job stays quiet
- **both** boundary lines, because a dashboard reads run history from the log
  and a run that starts without ending reads as *hung*, not as *off*
- no push, because being nagged is the thing the user turned off

`tests/conftest.py` turns every feature explicitly on, so each test here
switches off only the one it is about.
"""

import importlib
import re
from pathlib import Path

import pytest

from scribejay.core import features, registry

SRC = Path(registry.__file__).resolve().parent.parent


def _disable(monkeypatch, name):
    monkeypatch.setenv(features.setting_key(name), "0")


def _run(monkeypatch, task_key, tmp_path):
    """Run a task's main() and return (exit code, its log lines).

    Read from the log FILE, not from caplog: setup_logger sets
    propagate=False, so caplog sees nothing — and the file is the artifact a
    dashboard actually parses, which is the thing these tests are about.
    conftest points LOGS_DIR at tmp_path.
    """
    module = importlib.import_module(f"scribejay.{task_key}")
    monkeypatch.setattr("sys.argv", [task_key])
    code = module.main()
    log = (tmp_path / f"{task_key}.log").read_text().splitlines()
    # Strip the "<timestamp> [LEVEL] " prefix the formatter adds.
    return code, [re.sub(r"^\S+ \S+ \[\w+\] ", "", line) for line in log]


# ---- the morning that motivated this ----------------------------------------

@pytest.mark.parametrize("task", registry.TASKS, ids=lambda t: t.key)
def test_a_disabled_task_exits_zero_and_logs_both_boundaries(task, monkeypatch, tmp_path):
    _disable(monkeypatch, task.requires[0])
    code, messages = _run(monkeypatch, task.key, tmp_path)

    assert code == 0
    assert any(m.startswith("Starting") for m in messages), \
        "a skipped run must still open its boundary — the dashboard keys off it"
    assert task.complete_line in messages, \
        "no ending line: the dashboard shows this task as hung, not as off"


@pytest.mark.parametrize("task", registry.TASKS, ids=lambda t: t.key)
def test_a_disabled_task_pushes_nothing(task, monkeypatch, tmp_path):
    # Being alerted about a source you switched off is the bug this phase
    # exists to fix, so make the push fatal rather than merely stubbed.
    module = importlib.import_module(f"scribejay.{task.key}")

    def _no_push(*a, **k):
        raise AssertionError(f"{task.key} pushed an alert while disabled")

    monkeypatch.setattr(module, "notify_failure", _no_push)
    _disable(monkeypatch, task.requires[0])
    assert _run(monkeypatch, task.key, tmp_path)[0] == 0


@pytest.mark.parametrize("task", registry.TASKS, ids=lambda t: t.key)
def test_a_disabled_task_says_why(task, monkeypatch, tmp_path):
    _disable(monkeypatch, task.requires[0])
    _, messages = _run(monkeypatch, task.key, tmp_path)
    skip = [m for m in messages if m.startswith("Skipping: ")]
    assert skip, "a quiet task must say why it was quiet"
    assert "turned off in settings" in skip[0]


def test_every_feature_off_is_eight_clean_skips(monkeypatch, tmp_path):
    # The plan's end-to-end check, run in-process: the whole schedule declined.
    for name in features.NAMES:
        monkeypatch.setenv(features.setting_key(name), "0")
    for task in registry.TASKS:
        code, messages = _run(monkeypatch, task.key, tmp_path)
        assert code == 0, task.key
        assert task.complete_line in messages, task.key


# ---- the guard is actually installed -----------------------------------------

@pytest.mark.parametrize("task", registry.TASKS, ids=lambda t: t.key)
def test_the_guard_runs_before_any_work(task):
    """Assert on the source, not on behaviour.

    A guard placed after the first gather would still pass every test above —
    the task would skip — while having already read the user's Chrome history
    or hit the Strava API to do it. Position is the property; only the source
    shows it.
    """
    text = (SRC / f"{task.key}.py").read_text()
    body = text[text.index("def main()"):]
    guard = body.index(f'registry.skip_if_disabled("{task.key}"')
    start = body.index('logger.info(f"Starting') if 'logger.info(f"Starting' in body \
        else body.index('logger.info("Starting')
    assert start < guard, "the guard must come after the opening boundary line"
    for call in ("fetch_", "collect_", "get_events", "my_address("):
        found = body.find(call)
        if found != -1:
            assert guard < found, f"{call} runs before the guard in {task.key}"


@pytest.mark.parametrize("task", registry.TASKS, ids=lambda t: t.key)
def test_the_registrys_boundary_line_matches_the_tasks_own(task):
    # registry.py repeats a string that lives in the task module. If the task's
    # wording changes, the skip path starts logging a line the dashboard does
    # not recognise — and nothing else would catch it.
    text = (SRC / f"{task.key}.py").read_text()
    assert f'"{task.complete_line}"' in text or \
        f'f"{task.complete_line}"' in text, \
        f"{task.key} no longer logs {task.complete_line!r} on its success path"


def test_every_task_module_exists_and_has_a_main():
    for task in registry.TASKS:
        module = importlib.import_module(f"scribejay.{task.key}")
        assert callable(module.main)


def test_the_table_covers_every_scheduled_module():
    """A new task file with no registry row is a task nobody can switch off —
    and, from Phase 5 on, one that never gets a plist generated."""
    entrypoints = set()
    for path in SRC.glob("*.py"):
        text = path.read_text()
        if re.search(r"^def main\(\) -> int:", text, re.M) and "setup_logger" in text:
            entrypoints.add(path.stem)
    entrypoints -= {"migrate"}   # a one-shot command, not a scheduled job
    assert entrypoints == set(registry.KEYS)


# ---- the table's own shape ---------------------------------------------------

def test_is_ready_reports_the_first_missing_feature():
    # One reason at a time. A user fixes one thing; the next run names the next.
    ok, reason = registry.is_ready("strava_download")
    assert ok is True and reason == ""


def test_a_task_needing_two_features_is_skipped_when_either_is_off(monkeypatch):
    assert registry.get("strava_download").requires == ("strava", "google_calendar")
    _disable(monkeypatch, "google_calendar")
    ok, reason = registry.is_ready("strava_download")
    assert ok is False
    assert "Google Calendar" in reason


def test_clickup_is_not_required_by_daily_commits(monkeypatch):
    # daily_commits enriches with ClickUp; it does not depend on it. A day of
    # commits with ClickUp switched off is still a day worth writing.
    assert "clickup" not in registry.get("daily_commits").requires
    _disable(monkeypatch, "clickup")
    assert registry.is_ready("daily_commits")[0] is True


def test_every_required_feature_is_a_real_one():
    for task in registry.TASKS:
        for name in task.requires:
            assert name in features.BY_NAME, f"{task.key} requires {name}"


def test_enabled_tasks_tracks_the_toggles(monkeypatch):
    assert len(registry.enabled_tasks()) == len(registry.TASKS)
    _disable(monkeypatch, "transcripts")
    keys = [t.key for t in registry.enabled_tasks()]
    assert "ai_chat_learnings" not in keys
    assert "claude_time_blocks" not in keys
    assert "daily_commits" in keys


def test_schedule_times_are_unique_and_valid():
    # Two jobs at one minute means two model loads at once on a Mac mini.
    times = [(t.hour, t.minute) for t in registry.TASKS]
    assert len(set(times)) == len(times)
    assert all(0 <= h < 24 and 0 <= m < 60 for h, m in times)


def test_an_unknown_task_raises():
    with pytest.raises(KeyError):
        registry.get("daily_horoscope")
