"""Read mail metadata from Gmail, for the correspondence record.

Two fetchers, both headers-only: `fetch_sent_metadata` for what he wrote, and
`fetch_inbox_metadata` for what arrived. They share `_service`, `_headers`,
`_local_stamp` and the whole list-then-get loop, which is why they live in one
module rather than in two that copy each other.

Mirrors LocalLLMAgent's agent/tools/gmail_read.py — just the metadata slice.
Everything else in that module (search, thread reads, a history watcher) is not
journaling and is not here.

**Library functions, not chat tools** — no TOOL_SCHEMA. The callers are
scribejay/daily_correspondence.py and the settings page's source preview.
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

# The inbound set. `From` is who wrote — the one fact a sent row never carries.
# `Message-ID` and `References` are how an answer is matched back to what it
# answers, without ever opening either message.
INBOX_METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Message-ID", "References")


def _headers(payload: dict, wanted_headers: tuple = SENT_METADATA_HEADERS) -> dict:
    wanted = {h.lower(): h for h in wanted_headers}
    out = {}
    for header in payload.get("headers") or []:
        key = wanted.get((header.get("name") or "").lower())
        if key:
            out[key] = header.get("value", "")
    return out


# Cap on one day's sent mail. Well clear of a heavy day; it exists so a bulk
# send can't turn one journaling run into hundreds of API calls.
SENT_MAX_RESULTS = 60

# Higher, because an inbox is heavier than a sent folder even after the
# category filter below. Still a cap, for the same reason.
INBOX_MAX_RESULTS = 100

# "Mail that arrived", not "mail still in the inbox". This runs at 5:20 the
# next morning, by which time anything already dealt with has often been
# archived — and an archived message he never answered is exactly the one
# worth recording. Excluding what he made himself is what leaves only inbound.
INBOX_SCOPE = "-in:sent -in:draft -in:spam -in:trash -in:chats"

# Gmail's own tab classification, used as the noise filter. A hand-kept list of
# newsletter senders would need a new entry every time he subscribes to
# something, and would never be finished — the same reject-list trap the web
# fetcher avoids (AGENTS.md). Gmail already sorts this, per account, for free.
INBOX_CATEGORY_FILTER = ("-category:promotions -category:social "
                         "-category:updates -category:forums")


def _window(start, end) -> str:
    """The day window as epoch seconds.

    Gmail's after:/before: operators take whole days in the *account's*
    timezone, which is not necessarily the machine's, and would silently
    slice the day wrong (docs/timezones.md). Epoch seconds are unambiguous.
    """
    return f"after:{int(start.timestamp())} before:{int(end.timestamp())}"


def _fetch_rows(query: str, wanted_headers: tuple, limit: int, build) -> dict:
    """List a window, fetch each message as metadata, and map it with `build`.

    Shared by both fetchers: the listing, the per-message get, the
    one-unreadable-message rule and the sort are identical, and only the query,
    the header set and the row shape differ. Errors come back as
    {"error": ...} and read as an empty day to the caller, like every other
    source here.
    """
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
                metadataHeaders=list(wanted_headers)).execute()
        except Exception:
            # One unreadable message must not cost the day's record.
            continue
        rows.append(build(message, _headers(message.get("payload") or {},
                                            wanted_headers)))
    rows.sort(key=lambda r: r["date"])
    return {"messages": rows, "count": len(rows)}


def _sent_row(message: dict, headers: dict) -> dict:
    return {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "date": _local_stamp(message.get("internalDate")),
        # A reply is answering; a new thread is deciding to reach out. The
        # second is the signal worth separating in the record.
        "is_reply": bool(headers.get("In-Reply-To")),
    }


def _inbox_row(message: dict, headers: dict) -> dict:
    return {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "date": _local_stamp(message.get("internalDate")),
        # Kept so an answer can be matched to what it answered without opening
        # either message. Never rendered — see scribejay/correspondence.py.
        "header_id": headers.get("Message-ID", ""),
        "references": headers.get("References", ""),
    }


def fetch_sent_metadata(start, end, limit: int = SENT_MAX_RESULTS) -> dict:
    """Sent messages between two local-aware datetimes, as metadata rows.

    No body, no snippet. Each row is {message_id, thread_id, to, cc, subject,
    date, is_reply}.
    """
    return _fetch_rows(f"in:sent {_window(start, end)}",
                       SENT_METADATA_HEADERS, limit, _sent_row)


def fetch_inbox_metadata(start, end, limit: int = INBOX_MAX_RESULTS) -> dict:
    """Mail that arrived between two local-aware datetimes, as metadata rows.

    No body, no snippet — the same promise the sent fetcher makes, and it
    matters more here: a sender picks their own subject line, so this is the
    one fetcher whose text is chosen by a stranger. Every field that reaches
    a page goes through `scribejay.core.text.safe_label` first.

    Each row is {message_id, thread_id, from, to, cc, subject, date,
    header_id, references}.
    """
    query = f"{INBOX_SCOPE} {INBOX_CATEGORY_FILTER} {_window(start, end)}"
    return _fetch_rows(query, INBOX_METADATA_HEADERS, limit, _inbox_row)
