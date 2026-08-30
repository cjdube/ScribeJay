"""Tests for scribejay/core/config.py — the settings seam every scribejay/*.py
module reads through instead of calling os.getenv or a JSON file directly.

Mirrors the loader-degradation, helper-fallback, and shipped-file-contract
slices of LocalLLMAgent's tests/test_prefs.py. Dropped job_search()/
brief_calendar_hours()/followed_teams()/project_instruction_files() (Wren-only
features with no ScribeJay counterpart) — the shipped-file section below only
asserts what ScribeJay's own modules actually consume: persona.user_name and
calendar.categories (the three roles code reads: work, fitness, fallback)."""

import json

import pytest

from scribejay.core import config as prefs


# ---- shipped defaults satisfy every consumer's contract ---------------------
# schema.STRUCTURED_DEFAULTS is data several modules consume at import time
# (scribejay/sinks/calendar.py's CATEGORY_COLORS,
# scribejay/calendar_colorizer.py's VALID_COLOR_IDS); these are the schema
# guard that keeps an edit from silently breaking one of them.

def test_shipped_defaults_load():
    assert isinstance(prefs.PREFS, dict) and prefs.PREFS, \
        "schema.STRUCTURED_DEFAULTS failed to load"


def test_persona_has_a_user_name():
    assert prefs.persona().get("user_name")


def test_calendar_categories_complete():
    categories = prefs.calendar_categories()
    assert categories, "no valid calendar categories"
    for c in categories:
        for field in ("name", "color_id", "color_name"):
            assert c.get(field), f"category {c} missing {field}"


def test_calendar_roles_present():
    roles = [c.get("role") for c in prefs.calendar_categories() if c.get("role")]
    # claude_time_blocks needs work, strava_download needs fitness,
    # calendar_colorizer needs exactly one fallback.
    for role in ("work", "fitness"):
        assert role in roles, f"no category has role {role!r}"
    assert roles.count("fallback") == 1, "exactly one category must have role 'fallback'"


# ---- loader degradation -----------------------------------------------------

def test_load_missing_file_returns_empty(tmp_path):
    assert prefs._load(tmp_path / "nope.json") == {}


def test_load_invalid_json_returns_empty_and_keeps_file(tmp_path):
    bad = tmp_path / "preferences.json"
    bad.write_text("{not json")
    assert prefs._load(bad) == {}
    # Unlike store.load_json, no quarantine rename — the file stays put.
    assert bad.exists() and bad.read_text() == "{not json"


def test_load_non_object_returns_empty(tmp_path):
    lst = tmp_path / "preferences.json"
    lst.write_text(json.dumps([1, 2]))
    assert prefs._load(lst) == {}


# ---- helper fallbacks -------------------------------------------------------

def test_helpers_degrade_on_empty_prefs(monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", {})
    assert prefs.section("calendar") == {}
    assert prefs.user_name() == "the user"
    assert prefs.calendar_categories() == []
    assert prefs.category_color_by_role("fallback", "11") == "11"


def test_category_color_by_role_finds_role():
    monkeypatch_categories = {"calendar": {"categories": [
        {"name": "Fitness", "color_id": "4", "role": "fitness"},
        {"name": "Work", "color_id": "1", "role": "work"},
    ]}}
    saved = prefs.PREFS
    try:
        prefs.PREFS = monkeypatch_categories
        assert prefs.category_color_by_role("fitness", "0") == "4"
        assert prefs.category_color_by_role("no-such-role", "0") == "0"
    finally:
        prefs.PREFS = saved


def test_calendar_categories_skips_malformed_entries(monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", {"calendar": {"categories": [
        {"name": "Work", "color_id": "1"},
        {"name": "no id"},
        {"color_id": "9"},
        "not a dict",
    ]}})
    assert [c["name"] for c in prefs.calendar_categories()] == ["Work"]


def test_calendar_categories_not_a_list_is_empty(monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", {"calendar": {"categories": "nope"}})
    assert prefs.calendar_categories() == []


def test_persona_defaults_when_absent(monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", {})
    assert prefs.persona() == {}


def test_section_ignores_non_dict_value(monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", {"calendar": "nope"})
    assert prefs.section("calendar") == {}


# ---- the four resolution layers ---------------------------------------------
# getenv() resolves env var > ~/.scribejay/config.json > schema default >
# caller default. conftest points SCRIBEJAY_CONFIG_DIR at a tmp dir per test,
# so "the settings file" below is always an empty one unless a test writes it.

def _write_settings(monkeypatch, tmp_path, document):
    """Put a settings file in place and re-resolve from it."""
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(document))
    prefs.reload()


def test_schema_default_is_used_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert prefs.getenv("OLLAMA_MODEL") == "gemma4"


def test_settings_file_beats_the_schema_default(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _write_settings(monkeypatch, tmp_path, {"model": {"ollama_model": "llama9"}})
    assert prefs.getenv("OLLAMA_MODEL") == "llama9"


def test_environment_beats_the_settings_file(monkeypatch, tmp_path):
    # The environment stays on top so a launchd plist can override one job, a
    # debugging run can override by hand, and this suite can monkeypatch.
    _write_settings(monkeypatch, tmp_path, {"model": {"ollama_model": "llama9"}})
    monkeypatch.setenv("OLLAMA_MODEL", "from-env")
    assert prefs.getenv("OLLAMA_MODEL") == "from-env"


def test_callers_default_is_the_last_resort(monkeypatch):
    # A key with no schema row and nothing configured falls through to the
    # caller. tests/test_schema.py forbids new call sites like this, but the
    # layer still has to behave.
    assert prefs.getenv("NO_SUCH_SETTING_AT_ALL", "fallback") == "fallback"
    assert prefs.getenv("NO_SUCH_SETTING_AT_ALL") is None


def test_empty_values_count_as_unset(monkeypatch, tmp_path):
    # A blank field left behind in a settings file, or an `X=` line in a .env,
    # must fall through to the default rather than resolving to "".
    _write_settings(monkeypatch, tmp_path, {"model": {"ollama_model": ""}})
    monkeypatch.setenv("OLLAMA_MODEL", "")
    assert prefs.getenv("OLLAMA_MODEL") == "gemma4"


def test_non_string_settings_come_back_as_strings(monkeypatch, tmp_path):
    # Callers coerce with int()/float()/Path(); one representation on the way
    # out means a JSON number and an env var behave the same.
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    _write_settings(monkeypatch, tmp_path, {"model": {"ollama_num_ctx": 4096}})
    assert prefs.getenv("OLLAMA_NUM_CTX") == "4096"


def test_per_task_backend_reads_its_own_table(monkeypatch, tmp_path):
    # SCRIBEJAY_<TASK>_BACKEND is one setting per task, so it lives in a table
    # rather than getting eight near-identical schema rows.
    monkeypatch.delenv("SCRIBEJAY_DAILY_COMMITS_BACKEND", raising=False)
    _write_settings(monkeypatch, tmp_path,
                    {"model": {"per_task": {"daily_commits": "gemini"}}})
    assert prefs.getenv("SCRIBEJAY_DAILY_COMMITS_BACKEND") == "gemini"
    assert prefs.getenv("SCRIBEJAY_STRAVA_DOWNLOAD_BACKEND") is None


def test_an_unreadable_settings_file_degrades_and_warns(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text("{not json")
    prefs.STARTUP_WARNINGS.clear()
    prefs.reload()
    # Degrading silently is the failure this repo keeps re-learning: the run
    # would just quietly use defaults. setup_logger drains these into the log.
    assert prefs.getenv("OLLAMA_MODEL") == "gemma4"
    assert any("unreadable" in w for w in prefs.STARTUP_WARNINGS)


def test_a_stale_env_file_alongside_settings_is_warned_about(monkeypatch, tmp_path):
    # The env layer wins, so a leftover .env silently overrides everything the
    # settings screen writes. migrate renames it away; until then, say so.
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"model": {"ollama_model": "x"}}))
    monkeypatch.setenv("SCRIBEJAY_ENV_FILE", str(tmp_path / "dotenv"))
    (tmp_path / "dotenv").write_text("OLLAMA_MODEL=y\n")
    prefs.STARTUP_WARNINGS.clear()
    prefs.reload()
    assert any(".env entries win" in w for w in prefs.STARTUP_WARNINGS)


# ---- preferences come from the settings file, falling back to the JSON ------

def test_settings_file_preferences_win_over_the_legacy_json(monkeypatch, tmp_path):
    _write_settings(monkeypatch, tmp_path, {"persona": {"user_name": "Robin"}})
    assert prefs.user_name() == "Robin"


def test_legacy_preferences_still_load_when_the_settings_file_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    prefs.reload()
    # config/preferences.json, or the committed example it falls back to.
    assert prefs.user_name() != "the user"
    assert prefs.calendar_categories()


# ---- writing ----------------------------------------------------------------

def test_set_value_and_flush_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    prefs.reload()
    prefs.set_value("GOOGLE_CALENDAR_ID", "work@example.com")
    prefs.flush()
    assert json.loads((tmp_path / "config.json").read_text()) \
        ["google"]["calendar_id"] == "work@example.com"
    assert prefs.getenv("GOOGLE_CALENDAR_ID") == "work@example.com"


def test_set_value_refuses_a_secret(monkeypatch, tmp_path):
    # The whole point of the split: a credential in this file would be a
    # credential in the user's backups.
    with pytest.raises(ValueError):
        prefs.set_value("STRAVA_CLIENT_SECRET", "nope")


def test_set_value_refuses_an_unknown_key():
    with pytest.raises(KeyError):
        prefs.set_value("NOT_IN_THE_SCHEMA", "x")


def test_set_preference_refuses_a_settings_section():
    with pytest.raises(KeyError):
        prefs.set_preference("model", {"backend": "gemini"})


def test_the_settings_file_is_owner_only(monkeypatch, tmp_path):
    # It holds no secrets, but it names every folder ScribeJay touches.
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    prefs.reload()
    prefs.set_value("TIMEZONE", "UTC")
    prefs.flush()
    assert oct((tmp_path / "config.json").stat().st_mode)[-3:] == "600"


def test_a_failed_save_leaves_the_old_file_intact(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    prefs.reload()
    prefs.set_value("TIMEZONE", "UTC")
    prefs.flush()
    before = (tmp_path / "config.json").read_text()

    class _Unserializable:
        pass

    prefs.set_value("TIMEZONE", _Unserializable())
    with pytest.raises(TypeError):
        prefs.flush()
    assert (tmp_path / "config.json").read_text() == before
    assert not list(tmp_path.glob(".*tmp")), "the temp file should be cleaned up"


# ---- resolve_path -------------------------------------------------------------
# A relative path setting meant "beside the checkout" for as long as ScribeJay
# was only ever a checkout. Installed with `uv tool install`, the package sits
# in site-packages, where that same relative path names a file nobody can write.
# Both meanings have to keep working, which is what these pin.

def test_resolve_path_leaves_an_absolute_path_alone(tmp_path):
    assert prefs.resolve_path(str(tmp_path / "x")) == tmp_path / "x"


def test_resolve_path_expands_a_tilde():
    from pathlib import Path
    assert prefs.resolve_path("~/x") == Path.home() / "x"


def test_a_relative_path_resolves_beside_the_checkout_when_it_is_there():
    # config/.env.example is committed, so it is the one relative path that is
    # certainly present in a checkout. An existing install's configured
    # `config/google_credentials.json` must keep resolving to the file it has
    # been using for a year.
    resolved = prefs.resolve_path("config/.env.example")
    assert resolved.exists()
    assert resolved.name == ".env.example"


def test_a_relative_path_with_no_file_beside_the_checkout_lands_in_the_config_dir(
        monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    assert prefs.resolve_path("google_credentials.json") == \
        tmp_path / "google_credentials.json"


# ---- a bare install boots with a complete set (Phase 6c) --------------------
# The defaults used to be a JSON file found on disk at import. sinks/calendar.py
# builds CATEGORY_COLORS from them at import, so an install that could not find
# that file booted with no calendar categories at all — not a crash, just a
# colorizer that classified nothing. There is no file to find now, and these
# pin that.

def test_no_settings_file_still_yields_a_full_category_list(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    prefs.reload()
    assert not prefs.config_path().exists()
    categories = prefs.calendar_categories()
    assert len(categories) == 11
    assert prefs.category_color_by_role("fallback", "0") != "0"
    assert prefs.persona().get("user_name")
    assert prefs.section("learnings")["excluded_domains"]


def test_a_user_section_replaces_the_shipped_one_whole(tmp_path, monkeypatch):
    """Not a key-by-key merge: a user who cuts the list to one means one."""
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(
        {"calendar": {"categories": [{"name": "Only", "color_id": "2"}]}}))
    prefs.reload()
    assert [c["name"] for c in prefs.calendar_categories()] == ["Only"]
    # An untouched section still comes from the schema.
    assert prefs.persona().get("user_name")


def test_mutating_what_section_returns_does_not_poison_the_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    prefs.reload()
    prefs.section("calendar")["categories"].clear()
    prefs.reload()
    assert len(prefs.calendar_categories()) == 11
