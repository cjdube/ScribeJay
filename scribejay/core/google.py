"""Shared Google OAuth helper for the calendar, gmail and youtube sources.

Consent asks only for what the switched-on sources use (wanted_scopes), and
the token cache is ScribeJay's own. Narrow on purpose: the token is a file any
program running as the user can read, and a refresh token from a published app
does not expire, so whatever it holds is what a stolen copy holds.
Sharing a token file with another tool is a trap: whichever side runs its
consent flow last overwrites the other's grant, and the broader grant is the
one that silently breaks.

First run opens a browser for consent and caches a token at GOOGLE_TOKEN_PATH.
Subsequent runs (including unattended launchd runs) reuse/refresh that token
silently — no browser interaction required after the first authorization.

Setup (one-time, manual):
  1. https://console.cloud.google.com/ -> create/select a project
  2. Enable "Google Calendar API", "Gmail API", and "YouTube Data API v3"
  3. OAuth consent screen -> External -> add your own email as a test user
  4. Credentials -> Create Credentials -> OAuth client ID -> Desktop app
  5. Download the JSON, save it at GOOGLE_CREDENTIALS_PATH
     (default ~/.scribejay/google_credentials.json)
  6. Run `scribejay consent` once (opens a browser, caches the token)

See docs/setup-google.md for the full walkthrough.

A cached token is locked to the scopes it was consented to. Turning a source
on or off therefore means `scribejay consent` (consent()), which revokes the
old grant before asking again — deleting the token file alone leaves that
grant live at Google with nothing on disk to show it exists.
"""

import os
import threading

import httplib2
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from scribejay.core import config

# Outbound timeout (seconds) for every Google API call. Without it the
# client's default httplib2 transport has NO timeout, which could hang a
# launchd run past its next scheduled fire.
GOOGLE_HTTP_TIMEOUT_S = int(config.getenv("GOOGLE_HTTP_TIMEOUT_S"))

_AUTH = "https://www.googleapis.com/auth/"

# One scope per Google feature in core/features.py. calendar.events, not
# calendar: every call ScribeJay makes is events() insert/list/patch, and the
# full scope also lets its holder delete calendars and change their sharing.
FEATURE_SCOPES = {
    "google_calendar": _AUTH + "calendar.events",
    "gmail": _AUTH + "gmail.readonly",
    "youtube": _AUTH + "youtube.readonly",
}

# Backs only the emailed fallbacks (a failed vault write, a failed push, the
# colorizer's failure notice), all of which go to BRIEF_TO_EMAIL and do
# nothing while it is empty — so that setting is what asks for this scope.
SEND_SCOPE = _AUTH + "gmail.send"

# A token consented before calendar narrowed holds the full scope, which covers
# the narrow one. Without this every existing install would read as missing a
# scope it can in fact use. Covering is not needing: extra_scopes still reports
# the full scope, so doctor can say the grant is wider than it has to be.
COVERED_BY = {_AUTH + "calendar.events": _AUTH + "calendar"}

REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def wanted_scopes() -> list[str]:
    """The scopes the switched-on sources use — what consent asks for."""
    from scribejay.core import features

    scopes = [scope for name, scope in FEATURE_SCOPES.items()
              if features.enabled(name)]
    if config.getenv("BRIEF_TO_EMAIL"):
        scopes.append(SEND_SCOPE)
    return scopes


def missing_scopes(granted) -> list[str]:
    """Wanted scopes the grant does not cover. A source whose scope is missing
    gets a 403 on every call while the others keep working."""
    return [s for s in wanted_scopes()
            if s not in granted and COVERED_BY.get(s) not in granted]


def extra_scopes(granted) -> list[str]:
    """Granted scopes beyond what the switched-on sources need — unused, or
    broader than needed (full calendar where calendar.events would do).
    Exposure for no benefit, so doctor warns about it."""
    wanted = set(wanted_scopes())
    return sorted(s for s in granted if s not in wanted)

# Cached for the life of the process so a multi-source task doesn't re-read
# the token file on every call.
_CACHED_CREDS: Credentials | None = None

# Serializes credential acquisition/refresh and service construction.
_LOCK = threading.Lock()

# Built Google API clients, cached per (api, version) — see build_service().
_SERVICES: dict[tuple[str, str], object] = {}


def get_credentials() -> Credentials:
    global _CACHED_CREDS
    with _LOCK:
        if _CACHED_CREDS and _CACHED_CREDS.valid:
            return _CACHED_CREDS

        # config.resolve_path, not _ROOT: installed as a tool, _ROOT is
        # site-packages, and a relative default would name a file inside the
        # wheel that the consent flow cannot write its token into.
        creds_path = config.resolve_path(config.getenv("GOOGLE_CREDENTIALS_PATH"))
        token_path = config.resolve_path(config.getenv("GOOGLE_TOKEN_PATH"))
        token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # The OAuth client-secret file is placed by hand (downloaded from Google
        # Cloud Console) and defaults to a world-readable 0644 — unlike every
        # other secret file in config/, which is 0600.
        if creds_path.exists():
            try:
                os.chmod(creds_path, 0o600)
            except OSError:
                pass

        creds = _CACHED_CREDS
        if creds is None and token_path.exists():
            # No scopes argument: a refresh then keeps what was granted. Passing
            # wanted_scopes() would send a refresh asking for a scope the grant
            # lacks, which Google refuses outright — taking every Google source
            # down with the one newly switched on, instead of only that one.
            creds = Credentials.from_authorized_user_file(str(token_path))

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not creds_path.exists():
                    raise FileNotFoundError(
                        f"Missing {creds_path}. Download an OAuth client (Desktop app) JSON from "
                        "Google Cloud Console and save it there — see scribejay/core/google.py docstring."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(creds_path), wanted_scopes())
                # Force the account chooser (prompt=select_account) so consent
                # can't silently bind to whatever account the browser happens to
                # be defaulted to — youtube.readonly only works on the account
                # that owns the target channel.
                port = int(config.getenv("GOOGLE_OAUTH_PORT"))
                creds = flow.run_local_server(port=port, prompt="select_account")
            token_path.write_text(creds.to_json())
            # Contains a refresh token — keep it readable only by the owner.
            os.chmod(token_path, 0o600)

        _CACHED_CREDS = creds
        return creds


def build_service(api: str, version: str):
    """Build (and cache per (api, version)) a Google API client. Built once
    per process; the underlying credentials refresh themselves. get_credentials()
    is called outside the service lock so the two locks never nest."""
    key = (api, version)
    service = _SERVICES.get(key)
    if service is not None:
        return service
    creds = get_credentials()
    with _LOCK:
        service = _SERVICES.get(key)
        if service is None:
            http = AuthorizedHttp(creds, http=httplib2.Http(timeout=GOOGLE_HTTP_TIMEOUT_S))
            service = build(api, version, http=http)
            _SERVICES[key] = service
        return service

def consent() -> list[str]:
    """Revoke the current grant, then consent to exactly wanted_scopes().

    Revoked first because a refresh token outlives its file: deleting the
    token alone leaves the old, broader grant live at Google. If the revoke
    fails for any reason but "already invalid", this stops with the token
    still on disk — the user can retry, and nothing was orphaned. Returns the
    scopes now granted. Opens a browser, so only ever run by hand.
    """
    global _CACHED_CREDS
    token_path = config.resolve_path(config.getenv("GOOGLE_TOKEN_PATH"))
    if token_path.exists():
        old = Credentials.from_authorized_user_file(str(token_path))
        token = old.refresh_token or old.token
        if token:
            resp = requests.post(REVOKE_URL, params={"token": token},
                                 timeout=GOOGLE_HTTP_TIMEOUT_S)
            if resp.status_code != 200 and "invalid_token" not in resp.text:
                raise RuntimeError(f"Google refused the revoke ({resp.status_code}: "
                                   f"{resp.text[:200]}); {token_path} is unchanged")
        token_path.unlink()
    with _LOCK:
        _CACHED_CREDS = None
        _SERVICES.clear()
    return list(get_credentials().scopes or [])


if __name__ == "__main__":
    consent()
    print("Google OAuth token cached successfully.")
