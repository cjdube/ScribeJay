"""Tests for scribejay/core/config.py — the settings seam every scribejay/*.py
module reads through instead of calling os.getenv or a JSON file directly.

Mirrors the loader-degradation, helper-fallback, and shipped-file-contract
slices of LocalLLMAgent's tests/test_prefs.py. Dropped job_search()/
brief_calendar_hours()/followed_teams()/project_instruction_files() (Wren-only
features with no ScribeJay counterpart) — the shipped-file section below only
asserts what ScribeJay's own modules actually consume: persona.user_name and
calendar.categories (the three roles code reads: work, fitness, fallback)."""

import json

from scribejay.core import config as prefs


# ---- shipped file satisfies every consumer's contract -----------------------
# config/preferences.example.json is committed data several modules consume
# at import time (scribejay/sinks/calendar.py's CATEGORY_COLORS,
# scribejay/calendar_colorizer.py's VALID_COLOR_IDS); these are the schema
# guard that keeps an edit from silently breaking one of them.

def test_shipped_file_parses():
    assert isinstance(prefs.PREFS, dict) and prefs.PREFS, \
        "config/preferences.example.json failed to load"


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
