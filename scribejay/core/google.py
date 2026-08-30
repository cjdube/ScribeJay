"""Shared Google OAuth helper for the calendar, gmail and youtube sources.

Mirrors LocalLLMAgent's agent/tools/google_auth.py, with a narrower SCOPES
list (see below) and its own token cache — a separate consent from Wren's, on
purpose. A shared token file is a trap: if this repo ever ran its own consent
flow against Wren's token it would overwrite Wren's broader grant and break
its mail watcher.

First run opens a browser for consent and caches a token at GOOGLE_TOKEN_PATH.
Subsequent runs (including unattended launchd runs) reuse/refresh that token
silently — no browser interaction required after the first authorization.

Setup (one-time, manual):
  1. https://console.cloud.google.com/ -> create/select a project
  2. Enable "Google Calendar API", "Gmail API", and "YouTube Data API v3"
  3. OAuth consent screen -> External -> add your own email as a test user
  4. Credentials -> Create Credentials -> OAuth client ID -> Desktop app
  5. Download the JSON, save it as config/google_credentials.json
  6. Run: python -m scribejay.core.google   (opens browser once, caches token)

Adding a new scope to SCOPES below requires deleting the cached
config/google_token.json and re-running this module once — a cached token is
locked to the scopes it was originally consented to.
"""

import os
import threading

import httplib2
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

# Narrower than Wren's: no pubsub (mail watcher) and no tasks (Google Tasks) —
# neither is journaling. gmail.send backs the colorizer's failure email and
# the vault-write fallback; gmail.readonly backs daily_correspondence.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

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
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not creds_path.exists():
                    raise FileNotFoundError(
                        f"Missing {creds_path}. Download an OAuth client (Desktop app) JSON from "
                        "Google Cloud Console and save it there — see scribejay/core/google.py docstring."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
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

if __name__ == "__main__":
    get_credentials()
    print("Google OAuth token cached successfully.")
