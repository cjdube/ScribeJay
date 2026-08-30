"""Shared boilerplate for the HTTP-backed source modules.

Grew out of LocalLLMAgent's agent/tools/_http.py: locate config/.env and load
it, resolve an API key, funnel request exceptions into a uniform
{"error": ...} shape, and a main() that prints the JSON result and exits
non-zero on error.

No longer a verbatim mirror: resolve_key() gained the macOS Keychain, which
is where ScribeJay keeps credentials now that it has to be installable by
someone who never edits a .env file.
"""

import json
import os

import requests
from dotenv import load_dotenv

from scribejay.core import config, secrets


def load_env() -> None:
    """Load config/.env so os.getenv() sees keys from the file and the env.

    scribejay/core/config.py already does this at import and owns where the
    file lives; this stays as the name the source modules call, and defers to
    that one resolver rather than keeping a second copy of the path."""
    load_dotenv(config.env_path())


def resolve_key(name: str, arg: str | None = None) -> str | None:
    """Resolve a credential: explicit arg > environment > macOS Keychain.

    load_env() folds config/.env into the process environment, so one
    os.getenv() covers both a legacy .env entry and a real environment
    variable. The Keychain sits underneath both, so an env var still wins for
    a one-off debugging run, and the setup wizard's stored credential is what
    every scheduled run actually uses.

    The Keychain is only consulted when the earlier layers miss, so an
    ordinary run costs at most one `security` call per credential.
    """
    return arg or os.getenv(name) or secrets.get(name)


def missing_key_error(name: str) -> dict:
    """The uniform error dict returned when a required key can't be resolved."""
    return {"error": f"{name} not set (checked arg, config/.env, env var, Keychain)"}


def http_error(exc: Exception, phase: str = "fetch") -> dict:
    """Map a requests exception (or any other) onto the uniform error dict the
    source entrypoints return. Reproduces the messages the sources used inline:
    HTTP status when available, "network error" for other request failures,
    and "<phase> error" as the catch-all."""
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = exc.response
        status = resp.status_code if resp is not None else "?"
        return {"error": f"HTTP {status}: {exc}"}
    if isinstance(exc, requests.exceptions.RequestException):
        return {"error": f"network error: {exc}"}
    return {"error": f"{phase} error: {exc}"}


def print_result(result: dict) -> int:
    """Print a tool result as pretty JSON; return a non-zero exit code if it
    carries an error. Shared by every source module's main()."""
    print(json.dumps(result, indent=2))
    return 1 if "error" in result else 0
