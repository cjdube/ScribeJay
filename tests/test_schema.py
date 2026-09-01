"""Tests for scribejay/core/schema.py — the settings table every other
consumer reads.

The interesting test here is the drift guard: the schema is only a single
source of truth if the code cannot read a setting that has no row. A new
`config.getenv("SOMETHING_NEW", "fallback")` call site would otherwise work
perfectly while being invisible to the wizard, the settings screen, and the
migration — and the way that failure shows up is a user configuring a value in
the UI that the code never reads.
"""

import ast
from pathlib import Path

import pytest

from scribejay.core import config, schema
from scribejay.correspondence import DEFAULT_CORRESPONDENCE_DIR
from scribejay.sinks.vault import DEFAULT_LEARNINGS_DIR
from scribejay.sources.git import DEFAULT_PROJECTS_DIR
from scribejay.sources.transcripts import DEFAULT_GEMINI_DIR

_SOURCE_ROOT = Path(schema.__file__).resolve().parent.parent


def _getenv_keys() -> set[str]:
    """Every literal key passed to config.getenv() anywhere in scribejay/.

    An AST walk rather than a grep so a key split across lines or wrapped in
    int()/Path() is still found, and so a non-literal argument (the per-task
    backend f-string) is skipped rather than mis-parsed."""
    keys: set[str] = set()
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            is_getenv = (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "config"
            )
            if is_getenv and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    return keys


def test_every_setting_the_code_reads_has_a_row():
    missing = sorted(k for k in _getenv_keys() if schema.get(k) is None)
    assert not missing, (
        f"read through config.getenv but absent from scribejay/core/schema.py: "
        f"{missing}. Add a row, or the setting is invisible to the settings "
        f"screen, the wizard and the migration."
    )


def test_the_drift_guard_can_actually_see_call_sites():
    # A guard that silently found nothing would pass forever. Pin it to keys
    # that are unambiguously read this way today.
    found = _getenv_keys()
    assert {"LEARNINGS_DIR", "OLLAMA_MODEL", "TIMEZONE"} <= found


# ---- the table's own invariants ---------------------------------------------

def test_keys_are_unique():
    keys = [s.key for s in schema.SETTINGS]
    assert len(keys) == len(set(keys))


def test_section_and_name_pairs_are_unique():
    # Two rows landing on one settings-file slot would make each overwrite the
    # other on save, silently.
    slots = [(s.section, s.name) for s in schema.SETTINGS if not s.secret]
    assert len(slots) == len(set(slots))


def test_every_feature_is_declared():
    for s in schema.SETTINGS:
        assert s.feature in schema.FEATURES, f"{s.key} has unknown feature {s.feature}"


def test_every_row_has_a_label_and_help():
    # Both are rendered by the settings screen; an empty one ships a blank field.
    for s in schema.SETTINGS:
        assert s.label and s.help, f"{s.key} is missing a label or help text"


def test_choice_rows_declare_their_choices():
    for s in schema.SETTINGS:
        if s.type == "choice":
            assert s.choices, f"{s.key} is a choice with no choices"


def test_secrets_carry_no_default():
    # A default credential would be a shipped credential.
    for s in schema.SETTINGS:
        if s.secret:
            assert s.default is None, f"{s.key} is a secret with a default"


def test_backend_has_no_default_so_unset_stays_visible():
    # core/model.py logs `from unset` to say nobody chose a backend. A schema
    # default here would resolve to the same model while reporting that
    # someone had picked it — see docs/llm-backend.md.
    assert schema.default_for("SCRIBEJAY_LLM_BACKEND") is None


# ---- defaults agree with the constants the modules still expose --------------
# Both spellings are legitimate: the constant is a function default in a
# signature, the schema row is what the settings screen shows. They just must
# not disagree, which is what this pins.

@pytest.mark.parametrize("key,constant", [
    ("LEARNINGS_DIR", DEFAULT_LEARNINGS_DIR),
    ("CORRESPONDENCE_DIR", DEFAULT_CORRESPONDENCE_DIR),
    ("PROJECTS_DIR", DEFAULT_PROJECTS_DIR),
    ("SCRIBEJAY_GEMINI_CHATS_DIR", DEFAULT_GEMINI_DIR),
])
def test_schema_default_matches_the_module_constant(key, constant):
    assert schema.default_for(key) == constant


def test_secret_keys_cover_the_aliases():
    # GOOGLE_API_KEY has no row of its own but is a real credential name the
    # Gemini backend reads, so the Keychain and the migration must both treat
    # it as a secret.
    assert schema.is_secret("GOOGLE_API_KEY")
    assert "GOOGLE_API_KEY" not in schema.BY_KEY


def test_by_feature_and_sections_stay_in_declaration_order():
    # The feature toggle is last on purpose: the wizard and the settings screen
    # both read this order, and "do you want Strava" reads better after the
    # fields it governs than before them.
    strava = [s.key for s in schema.by_feature("strava")]
    assert strava == ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET",
                      "STRAVA_REFRESH_TOKEN", "SCRIBEJAY_FEATURE_STRAVA"]
    assert schema.sections()[0] == "core"


def test_no_secret_section_appears_in_the_settings_file_layout():
    # sections() drives what the settings file is allowed to contain; a secret's
    # section leaking in would invite a writer to put the value there.
    secret_sections = {s.section for s in schema.SETTINGS if s.secret}
    assert not (secret_sections & set(schema.sections()) & {"strava", "clickup"})


def test_config_can_resolve_every_row():
    # Nothing here raises: a row whose default is unparseable would blow up the
    # first task that reads it, at 4:30 AM, unattended.
    for s in schema.SETTINGS:
        if not s.secret:
            config.getenv(s.key)


def test_env_example_documents_every_key_and_agrees_on_defaults():
    """`config/.env.example` is one of the four consumers the schema docstring
    names, and the only one a human reads before anything runs. It drifts
    silently: SESSION_BLOCK_MIN_MINUTES sat at 5 there while the code used 10,
    so the file told users the wrong number for as long as it existed.
    """
    from pathlib import Path

    text = (Path(schema.__file__).resolve().parent.parent.parent
            / "config" / ".env.example").read_text()

    documented = {}
    for line in text.splitlines():
        line = line.strip().lstrip("# ").split("#")[0].strip()
        if "=" in line and not line.startswith("-"):
            key, _, value = line.partition("=")
            if key.strip().isupper():
                documented[key.strip()] = value.strip()

    missing = [s.key for s in schema.SETTINGS if s.key not in documented]
    assert not missing, f"settings with no line in .env.example: {missing}"

    # Only where the example states a value: a commented-out credential or an
    # intentionally blank line documents the key without claiming a default.
    disagree = {
        s.key: (documented[s.key], s.default)
        for s in schema.SETTINGS
        if not s.secret and documented[s.key] and s.default
        and documented[s.key].replace("~", str(Path.home())) != s.default
    }
    assert not disagree, f".env.example disagrees with the schema: {disagree}"


# Only up to what a sentence would plausibly spell out; a count past this is a
# KeyError, which is the right kind of loud.
_COUNT_WORDS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def test_env_example_states_the_real_number_of_secrets():
    """The same file's prose drifts too, and the key cross-check above cannot
    see it: `.env.example` said "the six secrets" while the schema held seven,
    from the run that added OPENROUTER_API_KEY. A sentence a human reads before
    anything runs is worth one assertion.
    """
    from pathlib import Path

    count = len([s for s in schema.SETTINGS if s.secret])
    text = (Path(schema.__file__).resolve().parent.parent.parent
            / "config" / ".env.example").read_text()

    assert f"the {_COUNT_WORDS[count]} secrets live in the macOS Keychain" in text, (
        f"the schema holds {count} secrets; .env.example says otherwise")
