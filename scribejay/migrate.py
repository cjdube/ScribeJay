"""Move an existing ScribeJay install off config/.env and onto the settings
file plus the macOS Keychain.

    python -m scribejay.migrate --dry-run    # show what would move
    python -m scribejay.migrate              # do it

Three things happen:

1. Every non-secret key in `config/.env` is written into
   `~/.scribejay/config.json`, in the section `scribejay/core/schema.py` gives
   it.
2. Every secret key is stored in the login Keychain and written nowhere else,
   which is the point of the exercise: the settings file ends up safe to back
   up, sync, or copy to a second machine.
3. `config/.env` is renamed to `config/.env.migrated`.

Step 3 is not tidiness. The environment layer sits ABOVE the settings file (see
`scribejay/core/config.py`), so a `.env` left in place would keep overriding
everything the settings screen writes, and the screen would look broken. The
file is renamed rather than deleted so a bad migration is one `mv` from being
undone.

Safe to run twice. The second run finds no `.env` and does nothing.

Keys in the `.env` that no longer exist in the schema are reported and left
alone in the renamed file rather than being dropped silently — a setting this
tool does not recognise is more likely a rename it has not been taught than
junk worth discarding.
"""

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values

from scribejay.core import config, schema, secrets

_ROOT = Path(__file__).resolve().parent.parent
LEGACY_PREFS_PATH = _ROOT / "config" / "preferences.json"


def env_path() -> Path:
    """The .env being migrated — config.env_path() so this and the loader can
    never disagree about which file is live."""
    return config.env_path()


def retired_env_path() -> Path:
    return env_path().with_name(env_path().name + ".migrated")


# Keys that changed name after this tool shipped. Without these an old .env's
# entry would be reported as unknown and the user would silently lose a tuned
# value — the exact failure a rename is supposed to be invisible against.
RENAMED = {
    "WREN_SESSION_BLOCK_GAP_MINUTES": "SCRIBEJAY_SESSION_BLOCK_GAP_MINUTES",
    "WREN_SESSION_BLOCK_MIN_MINUTES": "SCRIBEJAY_SESSION_BLOCK_MIN_MINUTES",
    "WREN_SESSION_BLOCK_MAX_CHARS": "SCRIBEJAY_SESSION_BLOCK_MAX_CHARS",
}


def _classify(values: dict) -> tuple[list, list, list, list]:
    """Sort .env entries into (settings, secrets, per-task backends, unknown).

    Entries are (key, value) pairs; a secret's value is carried but never
    printed."""
    settings, creds, per_task, unknown = [], [], [], []
    for key, value in values.items():
        if not value:
            continue
        key = RENAMED.get(key, key)
        if schema.is_secret(key):
            creds.append((key, value))
        elif schema.get(key) is not None:
            settings.append((key, value))
        elif key.startswith("SCRIBEJAY_") and key.endswith("_BACKEND"):
            task = key[len("SCRIBEJAY_"):-len("_BACKEND")].lower()
            per_task.append((task, value))
        else:
            unknown.append((key, value))
    return settings, creds, per_task, unknown


def _copy_preferences() -> list[str]:
    """Fold config/preferences.json into the settings document.

    This is now the ONLY reader of that file: since the shipped defaults moved
    into core/schema.py, config.reload() does not look at it at all. So an
    unmigrated checkout's edited categories reach the settings file here or
    nowhere, which is why this stays rather than being retired with the file
    it reads. It is left on disk rather than renamed — unlike the .env it
    shadows nothing, so a second copy is harmless."""
    copied = []
    if not LEGACY_PREFS_PATH.exists():
        return copied
    prefs = config._load(LEGACY_PREFS_PATH)
    for name in config._PREF_SECTIONS:
        value = prefs.get(name)
        if isinstance(value, dict) and value:
            config.set_preference(name, value)
            copied.append(name)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and change nothing")
    args = parser.parse_args()

    if not env_path().exists():
        print(f"No {env_path()} to migrate.")
        if config.config_path().exists():
            print(f"Settings already live in {config.config_path()}.")
        return 0

    settings, creds, per_task, unknown = _classify(dotenv_values(env_path()))

    print(f"Reading {env_path()}\n")
    for key, value in settings:
        print(f"  setting   {key} = {value}")
    for key, _ in creds:
        # Never the value: this output gets pasted into issues and screenshots.
        print(f"  KEYCHAIN  {key} (value hidden)")
    for task, value in per_task:
        print(f"  setting   model.per_task.{task} = {value}")
    for key, _ in unknown:
        print(f"  SKIPPED   {key} — not in scribejay/core/schema.py")

    if args.dry_run:
        print(f"\nDry run. Nothing written. Would write {config.config_path()} "
              f"and rename {env_path().name} to {retired_env_path().name}.")
        return 0

    if creds and not secrets.available():
        print("\nThis machine has no /usr/bin/security, so credentials cannot be "
              "stored. Migration stopped; nothing was changed.", file=sys.stderr)
        return 1

    # Credentials first. If the Keychain refuses, stop before the .env is
    # renamed — that way the only copy of a secret is never the one thrown away.
    for key, value in creds:
        if not secrets.set(key, value):
            print(f"\nCould not store {key} in the Keychain. Migration stopped; "
                  "nothing was changed.", file=sys.stderr)
            return 1

    for key, value in settings:
        config.set_value(key, value)
    for task, value in per_task:
        config.set_per_task_backend(task, value)
    copied = _copy_preferences()
    config.flush()

    env_path().rename(retired_env_path())
    # The document was built before the rename, and reload() cached a
    # .env-still-present warning while doing it. It is stale now.
    config.STARTUP_WARNINGS.clear()

    print(f"\nWrote {config.config_path()}")
    if creds:
        print(f"Stored {len(creds)} credential(s) in the login Keychain "
              f"under service '{secrets.SERVICE}'")
    if copied:
        print(f"Copied preference section(s): {', '.join(copied)}")
    print(f"Renamed {env_path().name} -> {retired_env_path().name} so it stops "
          "overriding your settings")
    print("\nRun the test suite and one task by hand to confirm nothing moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
