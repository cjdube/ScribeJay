"""Push a short notification to the user's phone via a self-hosted ntfy server.

This is ScribeJay's one outbound push channel — used to alert on a failed run,
since nothing else in this pipeline can reach out and an email failure notice
gets buried. Delivery is best-effort: notify() funnels every error into the
uniform {"error": ...} shape rather than raising, so a push outage can never
mask the task failure it's trying to report.

Mirrors LocalLLMAgent's agent/tools/notify.py, minus the push_log bookkeeping
(that store exists only to feed Wren's /pushes dashboard page — ScribeJay has
no page).

Config (config/.env):
    NTFY_URL   full topic URL, e.g. http://mac-mini.tailnet.ts.net:2586/scribejay-alerts
    NTFY_TOKEN publish token for that topic (Bearer). Optional but expected,
               since the self-hosted server runs auth-default-access: deny-all.
"""

from urllib.parse import urlsplit

import requests

from scribejay.core import config
from scribejay.core.http import http_error, load_env, resolve_key

# The title travels as an HTTP header and the message as the body; the body is
# sent UTF-8 encoded, and the title goes through _header_safe() below.
_TIMEOUT_S = 10
_MAX_MESSAGE_CHARS = 500


def _header_safe(title: str) -> str:
    """`title` reduced to characters an HTTP header can carry.

    latin-1 rather than ASCII on purpose — it covers the accented Latin letters
    that show up in real names and subjects, so "Café" survives intact and only
    the genuinely unencodable characters go."""
    cleaned = title.encode("latin-1", "ignore").decode("latin-1")
    # An all-emoji or all-CJK subject reduces to nothing, and a blank Title
    # header is worse than none: ntfy then shows the topic name instead.
    return " ".join(cleaned.split())


# Priority is a word in the plaintext header path but a 1-5 int in the JSON
# publish path (used when action buttons are attached).
_PRIORITY_INT = {"max": 5, "urgent": 5, "high": 4, "default": 3, "low": 2, "min": 1}


def _fallback_email(message: str, title: str | None, error: str) -> dict:
    """Best-effort email for a push that didn't send.

    Imported locally so notify() stays importable (and cheap) without pulling in
    the Google client stack on the overwhelmingly common success path."""
    from scribejay.sinks.email import send_email

    try:
        return send_email(
            subject=f"[push failed] {title or 'ScribeJay alert'}",
            body=f"{message}\n\n--\nntfy did not deliver this push: {error}",
        )
    except Exception as e:  # never let the fallback mask the original failure
        return {"error": str(e)}


def notify(
    message: str,
    title: str | None = None,
    priority: str | None = None,
    actions: list | None = None,
    email_fallback: bool = False,
) -> dict:
    """POST a notification to the configured ntfy topic. Returns {"ok": True}
    on success or {"error": ...} — never raises.

    `email_fallback` emails the message if the push fails, so an ntfy outage
    can't silently swallow an alert."""
    load_env()
    # Imported here rather than at module scope: core/features.py reaches back
    # into core/http.py, and notify is imported by core/logs.py, which almost
    # everything imports.
    from scribejay.core.features import enabled

    if not enabled("notify"):
        # Same reasoning as the unset URL below, one level up: a user who
        # switched push off in settings has not suffered a delivery failure.
        # Without this the toggle would be a lie whenever a URL is still set.
        return {"error": "push alerts are switched off"}

    url = config.getenv("NTFY_URL")
    if not url:
        # Deliberately no fallback: an unset NTFY_URL means push is switched
        # off on purpose, not that delivery failed.
        return {"error": "NTFY_URL not set"}

    token = resolve_key("NTFY_TOKEN")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    body = message[:_MAX_MESSAGE_CHARS]

    try:
        if actions:
            parts = urlsplit(url)
            payload = {"topic": parts.path.strip("/"), "message": body, "actions": actions}
            if title:
                payload["title"] = title
            if priority:
                payload["priority"] = _PRIORITY_INT.get(priority, 3)
            resp = requests.post(
                f"{parts.scheme}://{parts.netloc}", json=payload, headers=auth, timeout=_TIMEOUT_S)
        else:
            headers = dict(auth)
            safe_title = _header_safe(title) if title else ""
            if safe_title:
                headers["Title"] = safe_title
            if priority:
                headers["Priority"] = priority
            resp = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except Exception as e:
        result = http_error(e, phase="notify")
        if email_fallback:
            result["email_fallback"] = _fallback_email(body, title, result["error"])
        return result

    return {"ok": True}
