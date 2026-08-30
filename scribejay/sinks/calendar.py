"""Write to Google Calendar: log an activity, recolor an event by category.

Mirrors the write half of LocalLLMAgent's agent/tools/calendar.py.
`_local_timezone` there is an alias for `agent.dates.local_timezone`, not a
function of its own — this module imports the real thing directly (Trap 3 in
docs/reviews/scribejay-split-plan.md).
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from scribejay.core import config
from scribejay.core.dates import local_timezone
from scribejay.core.google import build_service

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / "config" / ".env")

# source_id prefix for the events scribejay/claude_time_blocks.py logs. It lives
# here, next to log_calendar_event (which owns source_id), so the writer and
# the colorizer that must leave those events alone can share it without either
# task importing the other.
SESSION_BLOCK_SOURCE_PREFIX = "claude-time:"

# Single source of truth for category -> (colorId, color name), defined in
# config/preferences.json. Also used by scribejay/calendar_colorizer.py to build
# its classification prompt.
CATEGORY_COLORS = {
    c["name"]: (c["color_id"], c["color_name"]) for c in config.calendar_categories()
}


def _human_when(start: str, end: str) -> str:
    """'Wednesday, August 19, 2026, 10:00 AM to 11:00 AM' for the ISO pair that
    was actually written, echoed back so the caller can see the write landed at
    all rather than restating the time from memory.

    Falls back to 'start to end' for anything Google accepted but datetime
    can't parse; this is a display string, and a write must never fail on it."""
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
    except (ValueError, TypeError):
        return f"{start} to {end}"
    day = s.strftime("%A, %B %-d, %Y")
    if e.date() != s.date():
        return f"{day}, {s.strftime('%-I:%M %p')} to {e.strftime('%A, %B %-d, %Y')}, {e.strftime('%-I:%M %p')}"
    return f"{day}, {s.strftime('%-I:%M %p')} to {e.strftime('%-I:%M %p')}"


def log_calendar_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    color_id: str = None,
    source_id: str = None,
) -> dict:
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    tz = local_timezone()

    try:
        service = build_service("calendar", "v3")

        if source_id:
            existing = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    privateExtendedProperty=f"source_id={source_id}",
                    singleEvents=True,
                )
                .execute()
            )
            items = existing.get("items", [])
            if items:
                return {
                    "event_id": items[0]["id"],
                    "html_link": items[0].get("htmlLink"),
                    "skipped": "event already logged for this source_id",
                }

        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz},
        }
        if color_id:
            body["colorId"] = color_id
        if source_id:
            body["extendedProperties"] = {"private": {"source_id": source_id}}

        created = service.events().insert(calendarId=calendar_id, body=body).execute()
    except Exception as e:
        return {"error": str(e)}

    return {
        "created": True,
        "summary": summary,
        "when": _human_when(start, end),
        "event_id": created.get("id"),
        "html_link": created.get("htmlLink"),
    }


def set_event_color(event_id: str, color_id: str) -> dict:
    """Patch just the colorId of an existing event — used by
    scribejay/calendar_colorizer.py to recolor yesterday's events by category."""
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    try:
        service = build_service("calendar", "v3")
        service.events().patch(
            calendarId=calendar_id, eventId=event_id, body={"colorId": color_id}
        ).execute()
    except Exception as e:
        return {"error": str(e)}

    return {"event_id": event_id, "color_id": color_id, "updated": True}
