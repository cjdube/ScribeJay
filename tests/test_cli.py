"""Tests for the `scribejay` console entrypoint.

The interesting behaviour is not the dispatch table — it is that `run` hands a
task the rest of the command line untouched. Every task has its own argparse
with its own --date/--backfill, and a CLI that swallowed those would quietly
remove the only way to re-run a missed day.
"""

import sys

import pytest

from scribejay import cli
from scribejay.core import registry


def test_every_registry_task_is_a_run_choice():
    # The choices come from the registry, so a new task is runnable the moment
    # it has a row — no second list to keep in step.
    args = cli.build_parser().parse_args(["run", "daily_commits"])
    assert args.task == "daily_commits"
    for key in registry.KEYS:
        assert cli.build_parser().parse_args(["run", key]).task == key


def test_run_calls_the_task_main_and_returns_its_code(monkeypatch):
    called = {}

    def fake_main():
        called["argv"] = list(sys.argv)
        return 7

    module = type("M", (), {"main": staticmethod(fake_main)})
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: module)

    assert cli.main(["run", "daily_commits"]) == 7
    assert called["argv"] == ["scribejay.daily_commits"]


def test_run_forwards_task_arguments(monkeypatch):
    seen = {}

    def fake_main():
        seen["argv"] = list(sys.argv)
        return 0

    module = type("M", (), {"main": staticmethod(fake_main)})
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: module)

    cli.main(["run", "daily_commits", "--date", "2026-08-29", "--backfill", "3"])
    assert seen["argv"] == ["scribejay.daily_commits", "--date", "2026-08-29",
                            "--backfill", "3"]


def test_run_restores_argv_even_when_the_task_raises(monkeypatch):
    """A task that blows up must not leave sys.argv rewritten. `scribejay run`
    is one process; a mangled argv would make anything after it — a traceback
    handler, a second call in a test — read the wrong command line."""
    def fake_main():
        raise RuntimeError("boom")

    module = type("M", (), {"main": staticmethod(fake_main)})
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: module)

    before = list(sys.argv)
    with pytest.raises(RuntimeError):
        cli.main(["run", "daily_commits", "--date", "2026-08-29"])
    assert sys.argv == before


def test_unknown_task_is_refused():
    with pytest.raises(SystemExit):
        cli.main(["run", "not_a_task"])


def test_stray_arguments_are_refused_outside_run(capsys):
    # parse_known_args is loose on purpose so `run` can forward a tail. That
    # looseness must not leak: a typo'd flag on `status` should be an error,
    # not silently ignored.
    assert cli.main(["status", "--wat"]) == 2
    assert "unrecognized" in capsys.readouterr().err


def test_every_documented_subcommand_parses():
    """The module docstring is the list a user reads. A command named there and
    missing from the parser is worse than one that was never mentioned."""
    for argv in (["init"], ["settings"], ["status"], ["schedule", "status"],
                 ["doctor"], ["doctor", "--probe"], ["migrate", "--dry-run"],
                 ["run", "daily_commits"]):
        assert cli.build_parser().parse_known_args(argv)


def test_status_runs(capsys):
    assert cli.main(["status"]) == 0
    assert "SOURCES" in capsys.readouterr().out


def test_migrate_passes_the_dry_run_flag(monkeypatch):
    seen = {}
    from scribejay import migrate

    monkeypatch.setattr(migrate, "main",
                        lambda: (seen.setdefault("argv", list(sys.argv)), 0)[1])
    cli.main(["migrate", "--dry-run"])
    assert seen["argv"] == ["scribejay.migrate", "--dry-run"]


def test_schedule_dispatches_to_the_named_action(monkeypatch):
    from scribejay.cli import schedule

    seen = []
    for action in ("install", "remove", "status"):
        monkeypatch.setattr(schedule, action,
                            lambda a=action: (seen.append(a), 0)[1])
    for action in ("install", "remove", "status"):
        assert cli.main(["schedule", action]) == 0
    assert seen == ["install", "remove", "status"]
