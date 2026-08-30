"""Tests for the generated launchd jobs.

Two things matter here and neither is XML.

The first is that the generated plists say exactly what the eight committed
ones said. Their labels and log basenames are identifiers a loaded agent and a
run-history dashboard are already keyed on, so a change to either is not a
refactor, it is an outage that reads as "the job stopped running".

The second is that only enabled tasks get installed, and that a task turned off
after being installed gets actively removed. Leaving a stale agent loaded would
put back the exact noise Phase 3 removed.
"""

import plistlib
import sys

import pytest

from scribejay.cli import schedule
from scribejay.core import features, registry

# The labels the eight committed plists carried, verbatim. Pinned as literals
# rather than recomputed from the same helper the code uses, because the point
# is that they have not changed — a test that derived them would agree with any
# renaming the code did.
COMMITTED_LABELS = {
    "ai_chat_learnings": "local.scribejay.aichatlearnings",
    "claude_time_blocks": "local.scribejay.claudetimeblocks",
    "daily_commits": "local.scribejay.dailycommits",
    "daily_youtube_learnings": "local.scribejay.dailyyoutubelearnings",
    "daily_chrome_learnings": "local.scribejay.dailychromelearnings",
    "daily_correspondence": "local.scribejay.dailycorrespondence",
    "strava_download": "local.scribejay.stravadownload",
    "calendar_colorizer": "local.scribejay.calendarcolorizer",
}


@pytest.fixture
def launchctl(monkeypatch):
    """Record launchctl calls instead of running them. The autouse guard in
    conftest raises; this replaces it for the tests that need the code path."""
    calls = []

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    def _record(*args):
        calls.append(args)
        return _Ok()

    monkeypatch.setattr(schedule, "_launchctl", _record)
    return calls


def test_labels_match_the_committed_plists():
    for key, label in COMMITTED_LABELS.items():
        assert schedule.label_for(key) == label
    assert set(COMMITTED_LABELS) == set(registry.KEYS)


def test_plist_carries_the_registry_schedule_and_module():
    task = registry.get("daily_commits")
    plist = schedule.plist_for(task)

    assert plist["Label"] == "local.scribejay.dailycommits"
    assert plist["ProgramArguments"] == [sys.executable, "-m", "scribejay.daily_commits"]
    assert plist["StartCalendarInterval"] == {"Hour": task.hour, "Minute": task.minute}
    # Never at load: install would otherwise start eight model jobs at once on
    # a machine the user is sitting at.
    assert plist["RunAtLoad"] is False


def test_the_interpreter_is_the_running_one():
    """launchd expands neither ~ nor $PATH, so the absolute path of whatever
    python ran `schedule install` is the only interpreter that will work — the
    tool's own venv after `uv tool install`, .venv/bin/python in a checkout."""
    for task in registry.TASKS:
        assert schedule.plist_for(task)["ProgramArguments"][0] == sys.executable


def test_log_basenames_did_not_change():
    # docs/architecture.md: run history is keyed off these names. The directory
    # moved to ~/.scribejay/logs in Phase 5; the basenames did not.
    for task in registry.TASKS:
        plist = schedule.plist_for(task)
        assert plist["StandardOutPath"].endswith(f"/{task.key}.launchd.log")
        assert plist["StandardErrorPath"] == plist["StandardOutPath"]


def test_log_paths_in_a_plist_are_absolute(monkeypatch):
    """launchd resolves a relative path against WorkingDirectory, so a relative
    SCRIBEJAY_LOGS_DIR would put the launchd log and the task's own run log in
    different places from one setting."""
    monkeypatch.setenv("SCRIBEJAY_LOGS_DIR", "logs")
    plist = schedule.plist_for(registry.get("daily_commits"))
    assert plist["StandardOutPath"].startswith("/")


def test_every_generated_plist_is_readable_by_plistlib(tmp_path):
    # A hand-built XML string would have been the obvious way to write these,
    # and the obvious way to ship a file launchd silently refuses to parse.
    for task in registry.TASKS:
        blob = plistlib.dumps(schedule.plist_for(task))
        assert plistlib.loads(blob)["Label"] == schedule.label_for(task.key)


def test_install_writes_only_enabled_tasks(monkeypatch, launchctl):
    monkeypatch.setenv(features.setting_key("strava"), "0")

    assert schedule.install() == 0

    assert not schedule.plist_path("strava_download").exists()
    assert schedule.plist_path("daily_commits").exists()
    loaded = plistlib.loads(schedule.plist_path("daily_commits").read_bytes())
    assert loaded["Label"] == "local.scribejay.dailycommits"


def test_install_removes_a_task_that_was_turned_off(monkeypatch, launchctl):
    schedule.install()
    assert schedule.plist_path("strava_download").exists()

    monkeypatch.setenv(features.setting_key("strava"), "0")
    schedule.install()

    assert not schedule.plist_path("strava_download").exists()
    assert ("bootout", f"{schedule._domain()}/local.scribejay.stravadownload") in launchctl


def test_install_reports_a_failed_bootstrap(monkeypatch, capsys):
    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "Load failed: 5: Input/output error"

    monkeypatch.setattr(schedule, "_launchctl", lambda *a: _Fail())

    assert schedule.install() == 1
    err = capsys.readouterr().err
    assert "FAILED" in err and "Input/output error" in err


def test_remove_deletes_every_generated_agent(launchctl):
    schedule.install()
    assert schedule.remove() == 0
    for task in registry.TASKS:
        assert not schedule.plist_path(task.key).exists()


def test_remove_leaves_selfheal_alone(launchctl):
    """It runs under Apple-signed /bin/bash so it survives a broken
    interpreter. A `schedule remove` that took it out would remove the one
    agent able to put the others back."""
    schedule.LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    selfheal = schedule.LAUNCH_AGENTS / f"{schedule.SELFHEAL_LABEL}.plist"
    selfheal.write_text("<plist/>")

    schedule.install()
    schedule.remove()

    assert selfheal.exists()


def test_install_never_boots_out_selfheal(launchctl):
    schedule.install()
    schedule.remove()
    booted = {args[1] for args in launchctl if args[0] == "bootout"}
    assert not any(label.endswith("selfheal") for label in booted)


def test_status_reports_on_disk_but_not_loaded(monkeypatch, capsys):
    """The two facts come apart, which is why they are printed separately: a
    brew upgrade leaves the file untouched and the job unexecutable."""
    monkeypatch.setattr(schedule, "_launchctl",
                        lambda *a: type("R", (), {"returncode": 0, "stdout": "",
                                                  "stderr": ""})())
    schedule.install()

    class _NotLoaded:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(schedule, "_launchctl", lambda *a: _NotLoaded())
    assert schedule.status() == 0
    assert "ON DISK, NOT LOADED" in capsys.readouterr().out


def test_a_source_checkout_keeps_the_repo_as_its_working_directory():
    # What the eight committed plists set, and what an existing install's
    # relative path settings still resolve against.
    assert (schedule.working_directory() / "pyproject.toml").exists()


def test_an_installed_copy_runs_from_the_config_dir_not_site_packages(
        monkeypatch, tmp_path):
    """A scheduled job whose cwd is inside the install is a job that can drop a
    stray file into site-packages."""
    monkeypatch.setattr(schedule, "_INSTALL_ROOT", tmp_path / "site-packages")
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path / "dot-scribejay"))
    assert schedule.working_directory() == tmp_path / "dot-scribejay"
