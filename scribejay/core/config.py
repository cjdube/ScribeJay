"""Settings seam for ScribeJay: every module reads settings and personal
preferences through here, never by calling os.getenv or a JSON file directly.

## Where a value comes from

`getenv()` resolves one key through four layers, first hit wins:

    1. environment variable   — a real env var, or a legacy config/.env entry
    2. ~/.scribejay/config.json — what the setup wizard and settings screen write
    3. the schema default     — scribejay/core/schema.py
    4. the caller's default   — a key with no schema row yet

The environment stays on top on purpose. It is what the test suite
monkeypatches, what a launchd plist can override for one job, and what a
`SCRIBEJAY_X=... python -m scribejay.foo` debugging run relies on. An empty
string counts as unset at every layer, so a blank field left in a settings
file falls through to the default instead of resolving to "".

Secrets are NOT here. They resolve through scribejay/core/secrets.py (the
macOS Keychain) via scribejay/core/http.py:resolve_key, so the settings file
holds no credentials and stays safe to back up.

## Why JSON and not TOML

The Phase 2 plan said `config.toml`. JSON won on three counts: `core/store.py`
already provides the atomic write and the corrupt-file quarantine this file
wants, Python ships a TOML reader but no writer (so TOML would mean either a
new dependency or a hand-rolled emitter for arrays-of-tables), and the
preferences half below is already JSON — one format means one document, one
writer, and one thing for the settings screen to save.

## Preferences

`persona`, `calendar` and `learnings` are personal, non-secret settings with
real structure (a list of calendar categories, lists of exclusions) rather
than flat scalars. They live in the same document, and until a user migrates
they are still read from the legacy config/preferences.json — which falls back
in turn to the committed config/preferences.example.json, so a fresh clone
boots with a valid schema before anyone has edited anything.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from scribejay.core import schema

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent

def env_path() -> Path:
    """The legacy config/.env file, folded into the process environment as
    layer 1. Overridable with SCRIBEJAY_ENV_FILE, which is how the test suite
    keeps away from the developer's real one — without that, every test in the
    repo silently resolves settings through whatever they happen to have
    configured, and a fresh clone and a working machine disagree about what
    the suite proves."""
    return Path(os.environ.get("SCRIBEJAY_ENV_FILE") or (_ROOT / "config" / ".env"))


# Loaded here rather than in each module so the layering has one entry point.
# load_dotenv does not overwrite a real environment variable.
load_dotenv(env_path())

# Sections of the settings document that hold preferences rather than flat
# settings. Named explicitly so merging them into PREFS cannot accidentally
# pull in [model] or [google] as though they were preferences.
_PREF_SECTIONS = ("persona", "calendar", "learnings")

# Problems found while loading, replayed into the first task logger that gets
# built (see scribejay/core/logs.py). Import happens before any logger is
# configured, so a warning emitted here would otherwise go nowhere — and a
# settings file that is being silently ignored is exactly the failure this
# module has to be loud about.
STARTUP_WARNINGS: list[str] = []


def config_dir() -> Path:
    """The directory holding config.json. Overridable with
    SCRIBEJAY_CONFIG_DIR — which is how tests keep away from the real one."""
    return Path(os.environ.get("SCRIBEJAY_CONFIG_DIR") or (Path.home() / ".scribejay"))


def config_path() -> Path:
    return config_dir() / "config.json"


def _load(path: Path) -> dict:
    """Read a JSON object from `path`, degrading to {} with an error log.

    Deliberately does NOT quarantine the file the way store.load_json does: a
    settings file is hand-editable, and moving a user's config aside because
    they left a trailing comma would turn a typo into lost configuration."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.error(f"could not load preferences from {path}: {e}")
        STARTUP_WARNINGS.append(f"settings file {path} is unreadable ({e}) — using defaults")
        return {}
    if not isinstance(data, dict):
        logger.error(f"preferences file {path} is not a JSON object")
        STARTUP_WARNINGS.append(f"settings file {path} is not a JSON object — using defaults")
        return {}
    return data


def _legacy_prefs_path() -> Path:
    path = _ROOT / "config" / "preferences.json"
    return path if path.exists() else _ROOT / "config" / "preferences.example.json"


# `_load` returns {} for a missing file, but the legacy loader's contract is
# that an absent preferences.json falls back to the committed example — so the
# path is chosen before loading, not after.
_PREFS_PATH = _legacy_prefs_path()

CONFIG: dict = {}
PREFS: dict = {}


def reload() -> None:
    """Re-read both files. Called at import, and again by anything that has
    just written the settings file (the migration, the settings screen)."""
    global CONFIG, PREFS, _PREFS_PATH
    CONFIG = _load(config_path())
    _PREFS_PATH = _legacy_prefs_path()
    merged = _load(_PREFS_PATH)
    for name in _PREF_SECTIONS:
        value = CONFIG.get(name)
        if isinstance(value, dict):
            merged[name] = value
    PREFS = merged

    legacy_env = env_path()
    if CONFIG and legacy_env.exists():
        # Both stores are live and the environment layer wins, so a stale .env
        # silently overrides anything the settings screen writes. `migrate`
        # renames the .env away once it has copied it; until then, say so.
        STARTUP_WARNINGS.append(
            f"both {config_path()} and {legacy_env} exist — .env entries win. "
            "Run `python -m scribejay.migrate` and let it retire the .env file."
        )


reload()


def _config_value(key: str):
    """The settings-file value for an env-var key, or None.

    Values come back as strings because the environment layer is a string
    layer, and one representation means callers keep the int()/float()/Path()
    coercion they already had."""
    setting = schema.get(key)
    if setting is not None:
        table = CONFIG.get(setting.section)
        if isinstance(table, dict):
            value = table.get(setting.name)
            if value is not None and value != "":
                return str(value)
        return None

    # The per-task model override, SCRIBEJAY_<TASK_KEY>_BACKEND, is one setting
    # per task rather than a fixed key, so it gets a table of its own instead
    # of a schema row for all eight tasks.
    if key.startswith("SCRIBEJAY_") and key.endswith("_BACKEND"):
        task = key[len("SCRIBEJAY_"):-len("_BACKEND")].lower()
        per_task = CONFIG.get("model", {}).get("per_task")
        if isinstance(per_task, dict):
            value = per_task.get(task)
            if value:
                return str(value)
    return None


def getenv(key: str, default=None):
    """Resolve one setting. See the module docstring for the four layers."""
    value = os.environ.get(key)
    if value:
        return value

    value = _config_value(key)
    if value:
        return value

    setting = schema.get(key)
    if setting is not None and setting.default is not None:
        return setting.default

    return default


def section(name: str) -> dict:
    value = PREFS.get(name)
    return value if isinstance(value, dict) else {}


def persona() -> dict:
    return section("persona")


def user_name() -> str:
    return persona().get("user_name", "the user")


def calendar_categories() -> list:
    """Category entries with at least a name and color_id; malformed ones skipped."""
    entries = section("calendar").get("categories", [])
    if not isinstance(entries, list):
        return []
    return [c for c in entries
            if isinstance(c, dict) and c.get("name") and c.get("color_id")]


def category_color_by_role(role: str, default: str) -> str:
    """colorId of the first category tagged with `role`, decoupling operational
    lookups from the personal category names, which a cloner is free to rename."""
    return next((c["color_id"] for c in calendar_categories()
                 if c.get("role") == role), default)


# ---- writing ---------------------------------------------------------------
# Used by the migration today and the settings screen in Phase 5. Reading is
# the hot path (every task, every run); writing happens during setup.

def save(data: dict) -> None:
    """Write the settings document, atomically and readable only by its owner.

    Atomic because a torn write would leave a user with no working config after
    a crash mid-save; 0600 because even without secrets this file names every
    folder ScribeJay touches."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def set_value(key: str, value) -> None:
    """Stage one setting into the in-memory document, by its env-var key.

    Does not save — a caller writing several values should save once, so a
    failure part-way leaves the old file intact rather than a half-updated one.
    Refuses secrets outright: they belong in the Keychain, and a helper that
    quietly accepted one would put a credential in a file that gets backed up."""
    if schema.is_secret(key):
        raise ValueError(f"{key} is a secret — store it with core.secrets.set()")
    setting = schema.get(key)
    if setting is None:
        raise KeyError(f"{key} is not in scribejay/core/schema.py")
    CONFIG.setdefault(setting.section, {})[setting.name] = value


def set_preference(name: str, value: dict) -> None:
    """Stage one preference section (persona, calendar, learnings)."""
    if name not in _PREF_SECTIONS:
        raise KeyError(f"{name} is not a preferences section {_PREF_SECTIONS}")
    CONFIG[name] = value


def set_per_task_backend(task_key: str, backend: str) -> None:
    """Stage the model backend for one task.

    SCRIBEJAY_<TASK_KEY>_BACKEND is one setting per task rather than a fixed
    key, so it gets a table of its own instead of eight near-identical schema
    rows that would have to be kept in step with the task list."""
    CONFIG.setdefault("model", {}).setdefault("per_task", {})[task_key.lower()] = backend


def flush() -> None:
    """Persist the staged document and re-resolve everything from it."""
    save(CONFIG)
    reload()
