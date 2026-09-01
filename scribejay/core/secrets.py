"""Credentials, kept in the macOS Keychain instead of in a file.

Every API key and token ScribeJay holds lives here, under one Keychain
service (`com.scribejay`) with the setting's key as the account name. Nothing
secret is written to `~/.scribejay/config.json`, which is what makes that file
safe to back up, sync, or copy to a second machine.

`scribejay/core/http.py:resolve_key` is the only caller on the read path, and
it is already the single credential lookup every source uses — so wiring the
Keychain in there covers Strava, ClickUp, Gemini and ntfy at once.

**Implemented with the `security` CLI, not a library.** `/usr/bin/security` is
Apple-signed, ships with macOS, and adds no dependency to a project whose whole
premise is running locally with a small install. The cost is that writing a
secret puts its value on a command line, where it is briefly visible to `ps` on
the same machine. That is an accepted trade for a single-user Mac where writes
happen a handful of times during setup; reads, which happen on every scheduled
run, expose nothing.

Everything here degrades to "no secret" rather than raising. A missing
`security` binary (someone running the test suite on Linux), a locked keychain,
or an absent item are all the same answer to the caller: `None`. A credential
that cannot be found is already handled everywhere as `missing_key_error`.

**No value is ever logged, printed, or included in an exception message.**
"""

import logging
import os
import subprocess

from scribejay.core import config

logger = logging.getLogger(__name__)


def service() -> str:
    """The Keychain service every ScribeJay item is filed under.

    One service for all of them; the account name is the setting key (e.g.
    STRAVA_CLIENT_SECRET), so `security` shows a readable row in Keychain
    Access. Overridable so a second install can use its own bucket.

    A function, and read through config.getenv rather than os.getenv: a module
    constant answered once at import, which no settings file could reach and
    which the schema's drift guard could not see — that guard walks
    config.getenv call sites, so a key read any other way is invisible to it.
    """
    return config.getenv("SCRIBEJAY_KEYCHAIN_SERVICE")

_SECURITY = "/usr/bin/security"

# Generous, but bounded: an unbounded call here would hang a launchd run, and
# the keychain either answers immediately or is prompting a user who is not
# there.
_TIMEOUT_S = 10


def available() -> bool:
    """Whether this machine has the Keychain CLI at all. False on Linux, and
    on a Mac stripped of the command line tools."""
    return os.path.exists(_SECURITY)


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess | None:
    """Run `security` with the given arguments. Returns None when the command
    could not be run at all, which callers treat the same as "not found"."""
    try:
        return subprocess.run(
            [_SECURITY, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        # The exception text can carry the argv, so log the type and nothing
        # else — a write's argv contains the secret.
        logger.debug(f"keychain call failed: {type(e).__name__}")
        return None


def get(name: str) -> str | None:
    """The stored secret for `name`, or None if there isn't one.

    `-w` prints just the password to stdout. Exit code 44 is
    errSecItemNotFound, the ordinary "you never set this" answer; anything else
    non-zero is worth a debug line but is still just None to the caller."""
    result = _run(["find-generic-password", "-s", service(), "-a", name, "-w"])
    if result is None:
        return None
    if result.returncode != 0:
        if result.returncode != 44:
            logger.debug(f"keychain lookup for {name} exited {result.returncode}")
        return None
    # `security` appends a newline to the value it prints; a secret with real
    # trailing whitespace is not a thing, so strip the line ending only.
    return result.stdout.rstrip("\n") or None


def set(name: str, value: str) -> bool:
    """Store (or replace) the secret for `name`. Returns whether it stuck.

    `-U` updates an existing item instead of failing on the duplicate, so this
    is safe to call again when a user re-pastes a rotated token."""
    if not value:
        return False
    result = _run([
        "add-generic-password",
        "-s", service(),
        "-a", name,
        "-U",
        "-D", "ScribeJay credential",
        "-w", value,
    ])
    if result is None or result.returncode != 0:
        code = "unavailable" if result is None else result.returncode
        logger.error(f"could not store {name} in the keychain (security exited {code})")
        return False
    return True


def delete(name: str) -> bool:
    """Remove the stored secret for `name`. Missing is success — the caller
    asked for it to be gone, and it is."""
    result = _run(["delete-generic-password", "-s", service(), "-a", name])
    if result is None:
        return False
    return result.returncode in (0, 44)


def is_set(name: str) -> bool:
    """Whether a secret exists, without handing the value back.

    This is what the settings screen renders: a stored credential is shown as
    'set', never as its value, so a shoulder-surfer or a screenshot cannot leak
    one."""
    return get(name) is not None
