"""Read sent-mail metadata from Gmail, for the correspondence record.

Mirrors LocalLLMAgent's agent/tools/gmail_read.py — just the sent-metadata
slice (`fetch_sent_metadata` and its helpers). Everything else in that module
(search, thread reads, the history watcher) is Wren-only and stays there.

A **library function, not a chat tool** — no TOOL_SCHEMA. The only caller is
scribejay/daily_correspondence.py.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from scribejay.core.dates import local_timezone
from scribejay.core.google import build_service


def _service():
    return build_service("gmail", "v1")


_MY_ADDRESS: dict = {}


def my_address() -> str:
    """The address of the mailbox we are reading, from the Gmail profile.

    Returns "" if the profile can't be read; the caller decides what that means.
    """
    if "value" not in _MY_ADDRESS:
        try:
            profile = _service().users().getProfile(userId="me").execute()
        except Exception:
            return ""
        _MY_ADDRESS["value"] = (profile.get("emailAddress") or "").strip()
    return _MY_ADDRESS["value"]


def _local_stamp(internal_date) -> str:
    """Gmail's internalDate is epoch MILLISECONDS in UTC. Day boundaries are
    local, so convert properly — never slice a UTC string against a local
    day (docs/timezones.md)."""
    try:
        moment = datetime.fromtimestamp(int(internal_date) / 1000,
                                        tz=ZoneInfo(local_timezone()))
    except (TypeError, ValueError, OSError):
        return ""
    return moment.strftime("%Y-%m-%d %H:%M")


# Headers a correspondence record needs, and deliberately nothing else. Gmail's
# format="metadata" honours this list server-side, so the bodies are never
# fetched at all rather than fetched and then discarded.
SENT_METADATA_HEADERS = ("To", "Cc", "Subject", "In-Reply-To")


def _headers(payload: dict) -> dict:
    wanted = {h.lower(): h for h in SENT_METADATA_HEADERS}
    out = {}
    for header in payload.get("headers") or []:
        key = wanted.get((header.get("name") or "").lower())
        if key:
            out[key] = header.get("value", "")
    return out


# Cap on one day's sent mail. Well clear of a heavy day; it exists so a bulk
# send can't turn one journaling run into hundreds of API calls.
SENT_MAX_RESULTS = 60


def fetch_sent_metadata(start, end, limit: int = SENT_MAX_RESULTS) -> dict:
    """Sent messages between two local-aware datetimes, as metadata rows.

    No body, no snippet. Each row is {message_id, thread_id, to, cc, subject,
    date, is_reply}. Errors come back as {"error": ...} and read as an empty
    day to the caller, like every other source here.

    The window is converted to epoch seconds rather than formatted as dates:
    Gmail's after:/before: operators take whole days in the *account's*
    timezone, which is not necessarily the machine's, and would silently
    slice the day wrong (docs/timezones.md).
    """
    query = f"in:sent after:{int(start.timestamp())} before:{int(end.timestamp())}"
    try:
        listed = _service().users().messages().list(
            userId="me", q=query, maxResults=limit).execute()
    except Exception as e:
        return {"error": str(e)}

    rows = []
    for stub in listed.get("messages") or []:
        try:
            message = _service().users().messages().get(
                userId="me", id=stub["id"], format="metadata",
                metadataHeaders=list(SENT_METADATA_HEADERS)).execute()
        except Exception:
            # One unreadable message must not cost the day's record.
            continue
        headers = _headers(message.get("payload") or {})
        rows.append({
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": _local_stamp(message.get("internalDate")),
            # A reply is answering; a new thread is deciding to reach out. The
            # second is the signal worth separating in the record.
            "is_reply": bool(headers.get("In-Reply-To")),
        })
    rows.sort(key=lambda r: r["date"])
    return {"messages": rows, "count": len(rows)}
