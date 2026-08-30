"""Settings seam for ScribeJay: every scribejay/*.py module reads env vars and
personal preferences through here, never by calling os.getenv or a shared
prefs module directly.

This is the seam the future setup wizard plugs into: Phase 2 of the ScribeJay
split (docs/reviews/scribejay-split-plan.md) swaps this module's backing store
from `.env` / config/preferences.json to `~/.scribejay/config.toml`, and
nothing else moves.

The preferences half is ScribeJay's own copy of LocalLLMAgent's agent/prefs.py
loader — not an import of it, since agent/ does not exist in this repo. The
real file is gitignored so personal details stay out of the repo;
config/preferences.example.json is the committed template and the fallback,
so a fresh clone boots with a valid schema before anyone has edited anything.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_PREFS_PATH = _ROOT / "config" / "preferences.json"
if not _PREFS_PATH.exists():
    _PREFS_PATH = _ROOT / "config" / "preferences.example.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.error(f"could not load preferences from {path}: {e}")
        return {}
    if not isinstance(data, dict):
        logger.error(f"preferences file {path} is not a JSON object")
        return {}
    return data


PREFS = _load(_PREFS_PATH)


def getenv(key: str, default=None):
    return os.getenv(key, default)


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
