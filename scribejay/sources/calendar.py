"""Read Google Calendar events for a time range.

Mirrors `get_events_in_range` from LocalLLMAgent's agent/tools/calendar.py —
used to categorize the prior day's events (daily_chrome_learnings,
calendar_colorizer).
"""

from scribejay.core import config
from scribejay.core.google import build_service


def get_events_in_range(time_min: str, time_max: str) -> dict:
    """List events between two ISO 8601 datetimes (inclusive), with colorId.

    Does NOT page: no maxResults, no pageToken loop, so Google's default page
    size of 250 is a silent ceiling. Deferred deliberately, not missed: the
    callers that matter are all narrow (the colorizer does yesterday, the
    learnings task a day)."""
    calendar_id = config.getenv("GOOGLE_CALENDAR_ID")

    try:
        service = build_service("calendar", "v3")
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        return {"error": str(e)}

    events = []
    for e in result.get("items", []):
        start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date"))
        end = e.get("end", {}).get("dateTime", e.get("end", {}).get("date"))
        events.append({
            "id": e.get("id"),
            "summary": e.get("summary", "(no title)"),
            "start": start,
            "end": end,
            "colorId": e.get("colorId"),
            "status": e.get("status"),
            # The id log_calendar_event stamped on events created by writing;
            # scribejay/calendar_colorizer.py uses it to skip the session
            # blocks, which arrive already colored.
            "source_id": e.get("extendedProperties", {}).get("private", {}).get("source_id"),
        })

    return {"event_count": len(events), "events": events}
