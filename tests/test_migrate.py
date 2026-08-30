"""Tests for scribejay/migrate.py — moving an existing install off config/.env.

This runs once per user, on data they cannot easily reconstruct, so the two
things worth pinning hardest are: a secret never reaches the settings file, and
a failure part-way through never destroys the .env that still holds the only
copy of a credential.

conftest points SCRIBEJAY_ENV_FILE and SCRIBEJAY_CONFIG_DIR at tmp paths, so
every test here builds its own .env rather than reading the developer's.
"""

import json

import pytest

from scribejay import migrate
from scribejay.core import config, secrets


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A throwaway .env at a path migrate will find, with settings live."""
    path = tmp_path / "dotenv"
    monkeypatch.setenv("SCRIBEJAY_ENV_FILE", str(path))
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path / "settings"))
    config.reload()
    return path


@pytest.fixture
def stored(monkeypatch):
    """Capture what would go to the Keychain instead of writing to it."""
    captured = {}
    monkeypatch.setattr(secrets, "set", lambda k, v: captured.__setitem__(k, v) or True)
    monkeypatch.setattr(secrets, "available", lambda: True)
    return captured


def _run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["migrate", *argv])
    return migrate.main()


def _written(tmp_path):
    return json.loads((tmp_path / "settings" / "config.json").read_text())


# ---- classification ---------------------------------------------------------

def test_settings_land_in_their_schema_section(env_file, stored, tmp_path, monkeypatch):
    env_file.write_text("OLLAMA_MODEL=llama9\nGOOGLE_CALENDAR_ID=work@example.com\n")
    assert _run(monkeypatch) == 0
    document = _written(tmp_path)
    assert document["model"]["ollama_model"] == "llama9"
    assert document["google"]["calendar_id"] == "work@example.com"


def test_secrets_go_to_the_keychain_and_nowhere_else(env_file, stored, tmp_path, monkeypatch):
    """The whole point of the migration. A credential in the settings file
    would be a credential in the user's backups and sync folders."""
    env_file.write_text("STRAVA_CLIENT_SECRET=shhh\nCLICKUP_API_TOKEN=pk_123\n")
    assert _run(monkeypatch) == 0
    assert stored == {"STRAVA_CLIENT_SECRET": "shhh", "CLICKUP_API_TOKEN": "pk_123"}
    assert "shhh" not in (tmp_path / "settings" / "config.json").read_text()
    assert "pk_123" not in (tmp_path / "settings" / "config.json").read_text()


def test_secret_values_are_never_printed(env_file, stored, monkeypatch, capsys):
    # This output gets pasted into issues and screenshots.
    env_file.write_text("STRAVA_CLIENT_SECRET=shhh\n")
    _run(monkeypatch)
    assert "shhh" not in capsys.readouterr().out


def test_per_task_backends_get_their_own_table(env_file, stored, tmp_path, monkeypatch):
    env_file.write_text("SCRIBEJAY_DAILY_COMMITS_BACKEND=gemini\n")
    assert _run(monkeypatch) == 0
    assert _written(tmp_path)["model"]["per_task"]["daily_commits"] == "gemini"


def test_unknown_keys_are_reported_and_kept(env_file, stored, tmp_path, monkeypatch, capsys):
    # More likely a rename this tool has not been taught than junk. Leaving it
    # in the renamed file means nothing is lost.
    env_file.write_text("SOME_RETIRED_KEY=value\n")
    assert _run(monkeypatch) == 0
    out = capsys.readouterr().out
    assert "SKIPPED   SOME_RETIRED_KEY" in out
    assert "SOME_RETIRED_KEY" not in json.dumps(_written(tmp_path))
    assert "SOME_RETIRED_KEY=value" in migrate.retired_env_path().read_text()


def test_empty_values_are_skipped(env_file, stored, tmp_path, monkeypatch):
    # A commented-out key left as `X=` must not be migrated as "".
    env_file.write_text("OLLAMA_MODEL=\nGOOGLE_CALENDAR_ID=work@example.com\n")
    assert _run(monkeypatch) == 0
    assert "ollama_model" not in _written(tmp_path).get("model", {})


# ---- the .env is retired ----------------------------------------------------

def test_the_env_file_is_renamed_so_it_stops_overriding(env_file, stored, monkeypatch):
    """Not tidiness. The environment layer sits ABOVE the settings file, so a
    .env left in place keeps overriding everything the settings screen writes
    and the screen looks broken."""
    env_file.write_text("OLLAMA_MODEL=llama9\n")
    assert _run(monkeypatch) == 0
    assert not env_file.exists()
    assert migrate.retired_env_path().read_text() == "OLLAMA_MODEL=llama9\n"


def test_running_twice_is_a_no_op(env_file, stored, monkeypatch):
    env_file.write_text("OLLAMA_MODEL=llama9\n")
    assert _run(monkeypatch) == 0
    assert _run(monkeypatch) == 0


def test_dry_run_changes_nothing(env_file, stored, tmp_path, monkeypatch, capsys):
    env_file.write_text("OLLAMA_MODEL=llama9\nSTRAVA_CLIENT_SECRET=shhh\n")
    assert _run(monkeypatch, "--dry-run") == 0
    assert env_file.exists()
    assert not (tmp_path / "settings" / "config.json").exists()
    assert stored == {}
    assert "Dry run" in capsys.readouterr().out


# ---- failure never destroys the only copy of a credential -------------------

def test_a_keychain_failure_stops_before_the_env_is_renamed(env_file, monkeypatch):
    """The .env still holds the only copy of the secret at this point. Renaming
    it after a failed store would be the one way this tool could lose data."""
    monkeypatch.setattr(secrets, "available", lambda: True)
    monkeypatch.setattr(secrets, "set", lambda k, v: False)
    env_file.write_text("OLLAMA_MODEL=llama9\nSTRAVA_CLIENT_SECRET=shhh\n")
    assert _run(monkeypatch) == 1
    assert env_file.exists()
    assert not migrate.retired_env_path().exists()


def test_no_keychain_at_all_stops_before_anything_moves(env_file, tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "available", lambda: False)
    env_file.write_text("STRAVA_CLIENT_SECRET=shhh\n")
    assert _run(monkeypatch) == 1
    assert env_file.exists()
    assert not (tmp_path / "settings" / "config.json").exists()


def test_no_keychain_is_fine_when_there_are_no_secrets(env_file, tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "available", lambda: False)
    env_file.write_text("OLLAMA_MODEL=llama9\n")
    assert _run(monkeypatch) == 0
    assert _written(tmp_path)["model"]["ollama_model"] == "llama9"


# ---- preferences ------------------------------------------------------------

def test_preferences_are_copied_into_the_settings_file(env_file, stored, tmp_path, monkeypatch):
    prefs = tmp_path / "preferences.json"
    prefs.write_text(json.dumps({"persona": {"user_name": "Robin"}, "ignored": {"x": 1}}))
    monkeypatch.setattr(migrate, "LEGACY_PREFS_PATH", prefs)
    env_file.write_text("OLLAMA_MODEL=llama9\n")
    assert _run(monkeypatch) == 0
    document = _written(tmp_path)
    assert document["persona"] == {"user_name": "Robin"}
    assert "ignored" not in document


def test_no_stale_env_warning_survives_the_migration(env_file, stored, monkeypatch):
    # reload() caches the "both files exist" warning while building the
    # document, and the rename happens after. Leaving it queued would push a
    # confusing warning into the next task's log.
    env_file.write_text("OLLAMA_MODEL=llama9\n")
    assert _run(monkeypatch) == 0
    assert not any(".env entries win" in w for w in config.STARTUP_WARNINGS)
