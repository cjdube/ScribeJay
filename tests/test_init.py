"""Tests for `scribejay init`.

The wizard is a decision tree that writes settings, so the tests drive it with
scripted answers through `Console` rather than through a subprocess. Three
behaviours carry the weight:

  - a **no** is recorded as a no, not left for the probe to re-guess later;
  - the journal folder is created, and only inside a path the form allows;
  - quitting part way leaves the previous settings exactly as they were.

Nothing here opens a browser, touches launchd, or writes the real Keychain —
the autouse guards in conftest.py make all three loud, and the two steps that
would reach them are stubbed per test.
"""

import json

import pytest

from scribejay.cli import init
from scribejay.core import config, features, schema


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch, tmp_path):
    """The two steps that reach outside the process, replaced by recorders.

    `settings_server.serve()` opens a browser and binds a port;
    `schedule.install()` writes ~/Library/LaunchAgents and calls launchctl.
    Both are answered "no" in most scripts below, but a wizard that grew a
    default of yes must not silently start doing either in the suite.
    """
    from scribejay.cli import schedule, settings_server

    monkeypatch.setattr(settings_server, "serve", lambda *a, **k: 0)
    monkeypatch.setattr(schedule, "install", lambda: 0)
    # Chrome's real history database is on the developer's machine; the
    # wizard's disk-access step reads it for real.
    monkeypatch.setattr("scribejay.sources.chrome.HISTORY_PATH", tmp_path / "no-chrome")


@pytest.fixture
def ollama_up(monkeypatch):
    import requests

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": config.getenv("OLLAMA_MODEL")}]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())


def _script(tmp_path, **over):
    """A complete run: Tier 0 all on, ollama, no browser, no schedule."""
    answers = [
        str(tmp_path / "journal"),   # journal folder
        "",                          # correspondence — accept the default
        "y",                         # chrome
        "y",                         # transcripts
        "y", str(tmp_path / "repos"),  # git + its folder
        "n",                         # notify
        "1",                         # ollama
        "n",                         # settings screen
        "n",                         # schedule
    ]
    return over.get("answers", answers)


def _written():
    return json.loads(config.config_path().read_text())


# ---- the happy path ---------------------------------------------------------

def test_a_full_run_writes_folders_toggles_and_a_backend(tmp_path, ollama_up):
    console = init.Console(answers=_script(tmp_path))
    assert init.run(console) == 0

    document = _written()
    assert document["output"]["learnings_dir"] == str(tmp_path / "journal")
    assert document["features"]["chrome"] == "1"
    assert document["model"]["backend"] == "ollama"
    assert document["git"]["projects_dir"] == str(tmp_path / "repos")


def test_the_journal_folder_is_created(tmp_path, ollama_up):
    """The one place in ScribeJay that makes a directory. Everywhere else a
    missing folder means the path is wrong."""
    target = tmp_path / "journal"
    assert not target.exists()
    init.run(init.Console(answers=_script(tmp_path)))
    assert target.is_dir()


def test_correspondence_defaults_beside_the_journal_not_inside_it(tmp_path, ollama_up):
    """Inside an Obsidian vault's ingest folder, sent-mail pages get swept up
    with the daily ones. The default is a sibling for that reason."""
    init.run(init.Console(answers=_script(tmp_path)))
    written = _written()["output"]["correspondence_dir"]
    assert written == str(tmp_path / "correspondence")
    assert not written.startswith(str(tmp_path / "journal"))


# ---- a no is a no -----------------------------------------------------------

def test_declining_a_source_is_recorded_not_left_unanswered(tmp_path, ollama_up):
    """The point of the wizard writing toggles at all. An unanswered toggle is
    re-guessed from the machine on every run, so a user who declines Strava in
    August gets it switched back on the day they install the Strava app."""
    answers = _script(tmp_path)
    answers[2] = "n"          # chrome
    init.run(init.Console(answers=answers))
    assert _written()["features"]["chrome"] == "0"


def test_a_recorded_no_survives_the_probe_saying_yes(tmp_path, ollama_up, monkeypatch):
    answers = _script(tmp_path)
    answers[2] = "n"
    init.run(init.Console(answers=answers))
    monkeypatch.setattr(features, "configured", lambda name: True)
    monkeypatch.delenv(features.setting_key("chrome"), raising=False)
    config.reload()
    assert features.enabled("chrome") is False


def test_declining_git_skips_its_follow_up_question(tmp_path, ollama_up):
    answers = [
        str(tmp_path / "journal"), "",
        "y", "y",
        "n",              # git — no folder question follows
        "n",              # notify
        "1", "n", "n",
    ]
    assert init.run(init.Console(answers=answers)) == 0
    assert _written()["features"]["git"] == "0"
    assert "git" not in _written()


# ---- folders ----------------------------------------------------------------

def test_a_forbidden_folder_is_refused_and_re_asked(tmp_path, ollama_up, capsys):
    """settings_form owns the list of paths ScribeJay will not write to. The
    wizard creates folders, so it has to be asking through that list — not
    around it."""
    answers = _script(tmp_path)
    answers[0:1] = [str(init.Path.home() / ".ssh" / "journal"),
                    str(tmp_path / "journal")]
    assert init.run(init.Console(answers=answers)) == 0
    assert not (init.Path.home() / ".ssh" / "journal").exists()
    assert "will not write to" in capsys.readouterr().out


# ---- the model ---------------------------------------------------------------

def test_choosing_a_cloud_backend_stores_the_key_in_the_keychain(tmp_path, monkeypatch):
    from scribejay.core import secrets

    stored = {}
    monkeypatch.setattr(secrets, "set",
                        lambda name, value: (stored.setdefault(name, value), True)[1])
    answers = _script(tmp_path)
    answers[7:8] = ["openrouter", "sk-test-123"]
    assert init.run(init.Console(answers=answers)) == 0

    assert stored == {"OPENROUTER_API_KEY": "sk-test-123"}
    # And nowhere else. A settings file holding a credential is the thing
    # Phase 2 removed.
    assert "sk-test-123" not in config.config_path().read_text()


def test_an_empty_key_is_skipped_rather_than_stored(tmp_path, monkeypatch, capsys):
    from scribejay.core import secrets

    monkeypatch.setattr(secrets, "set",
                        lambda *a: pytest.fail("stored an empty key"))
    answers = _script(tmp_path)
    answers[7:8] = ["gemini", ""]
    assert init.run(init.Console(answers=answers)) == 0
    assert "scribejay settings" in capsys.readouterr().out


def test_an_unreachable_ollama_is_reported_but_does_not_stop_setup(tmp_path,
                                                                   monkeypatch, capsys):
    """Ollama not being up during setup is normal — it is a separate app. The
    wizard says so and carries on rather than making the user restart it."""
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    assert init.run(init.Console(answers=_script(tmp_path))) == 0
    assert "not answering" in capsys.readouterr().out


# ---- quitting ----------------------------------------------------------------

def test_running_out_of_input_aborts(tmp_path):
    with pytest.raises(init.Aborted):
        init.run(init.Console(answers=[str(tmp_path / "journal")]))


def test_quitting_part_way_writes_nothing(tmp_path):
    """Answers are staged and flushed once. A wizard that wrote each answer as
    it arrived would leave a half-configured install behind a Ctrl-C."""
    assert not config.config_path().exists()
    with pytest.raises(init.Aborted):
        init.run(init.Console(answers=[str(tmp_path / "journal"), "", "y"]))
    assert not config.config_path().exists()


def test_main_turns_an_abort_into_exit_one(monkeypatch, capsys):
    monkeypatch.setattr(init, "Console", lambda *a, **k: object())
    monkeypatch.setattr(init, "run", lambda console: (_ for _ in ()).throw(
        init.Aborted("no more input")))
    assert init.main([]) == 1
    assert "Nothing was changed" in capsys.readouterr().err


def test_declining_the_overwrite_of_an_existing_settings_file(tmp_path, capsys):
    config.set_value("OLLAMA_MODEL", "keepme")
    config.flush()
    assert init.run(init.Console(answers=["n"])) == 1
    assert _written()["model"]["ollama_model"] == "keepme"


# ---- the console --------------------------------------------------------------

def test_confirm_re_asks_on_an_answer_it_does_not_understand(capsys):
    console = init.Console(answers=["maybe", "y"])
    assert console.confirm("Sure?") is True
    assert "answer y or n" in capsys.readouterr().out


def test_confirm_takes_the_default_on_enter():
    assert init.Console(answers=[""]).confirm("Sure?", default=False) is False
    assert init.Console(answers=[""]).confirm("Sure?", default=True) is True


def test_choose_accepts_a_number_or_a_name():
    assert init.Console(answers=["2"]).choose("x", ["a", "b"], "a") == "b"
    assert init.Console(answers=["b"]).choose("x", ["a", "b"], "a") == "b"
    assert init.Console(answers=[""]).choose("x", ["a", "b"], "a") == "a"


def test_choose_re_asks_on_an_out_of_range_number(capsys):
    assert init.Console(answers=["9", "1"]).choose("x", ["a", "b"], "a") == "a"
    assert "pick 1-2" in capsys.readouterr().out


# ---- wiring -------------------------------------------------------------------

def test_the_cli_dispatches_to_init(monkeypatch):
    from scribejay import cli

    monkeypatch.setattr(init, "main", lambda argv: 0)
    assert cli.main(["init"]) == 0


def test_every_tier_zero_feature_has_a_schema_row_for_its_toggle():
    """The wizard writes each answer through config.set_value, which refuses a
    key with no schema row. A new Tier 0 feature without one would abort setup
    at the question, not at import."""
    for feature in features.by_tier(features.TIER_NONE):
        assert schema.get(features.setting_key(feature.name)) is not None
