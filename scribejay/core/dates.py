"""Shared date-resolution helpers.

Mirrors the day-resolution slice of LocalLLMAgent's agent/dates.py. The local
model can't be trusted to know the current date or do weekday arithmetic, so
resolve_date() and local_timezone() live here in Python rather than being left
to a prompt.
"""

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


def local_timezone() -> str:
    """Resolve the system's IANA timezone name (e.g. 'America/New_York').

    Google Calendar also rejects abbreviations like 'EDT', so we read the real
    zoneinfo path via /etc/localtime rather than relying on tzinfo.__str__.
    Overridable with the TIMEZONE env var; falls back to 'UTC' if the path
    can't be resolved."""
    override = os.getenv("TIMEZONE")
    if override:
        return override
    try:
        resolved = Path("/etc/localtime").resolve()
        parts = resolved.parts
        idx = parts.index("zoneinfo")
        return "/".join(parts[idx + 1:])
    except (OSError, ValueError):
        return "UTC"


def prior_day(now: datetime | None = None) -> tuple[datetime, datetime, "date"]:
    """Return (start, end, day) for yesterday in local tz: start at 00:00:00 and
    end at 23:59:59 of the day before today, plus the date itself (for the output
    filename). `now` is injectable for tests. Anchored to "the calendar day before
    today", so a 5am launchd run covers all of yesterday."""
    tz = ZoneInfo(local_timezone())
    today = (now or datetime.now(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=1)
    end = start.replace(hour=23, minute=59, second=59)
    return start, end, start.date()


# Weekday name -> Python weekday() index (Mon=0).
_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

# Words that pin a weekday phrase to one direction, overriding `prefer`.
_BACKWARD_QUALIFIERS = {"last", "past", "previous"}
_FORWARD_QUALIFIERS = {"next", "this", "coming", "upcoming", "following"}

_RELATIVE_DAY_OFFSETS = {"today": 0, "tomorrow": 1, "yesterday": -1}


def _resolve_relative_day(text: str, today: date, prefer: str) -> Optional[date]:
    """Resolve a relative day phrase ('tomorrow', 'next tuesday') to a date, or
    None if it isn't one — in which case resolve_date() falls through to its
    numeric parsing, so no existing input changes behavior.

    'next tuesday' means the next Tuesday *after* today, not the Tuesday of the
    following calendar week — so asked on Monday it means tomorrow. A bare
    weekday has no direction of its own and follows the caller's `prefer`."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = re.sub(r"^(?:on\s+)?(?:the\s+)?", "", normalized)

    offset = _RELATIVE_DAY_OFFSETS.get(normalized)
    if offset is not None:
        return today + timedelta(days=offset)

    parts = normalized.split(" ")
    if len(parts) == 2 and parts[0] in _BACKWARD_QUALIFIERS | _FORWARD_QUALIFIERS:
        qualifier, name = parts
    elif len(parts) == 1:
        qualifier, name = "", parts[0]
    else:
        return None

    target = _WEEKDAYS.get(name)
    if target is None:
        return None

    if qualifier in _BACKWARD_QUALIFIERS:
        backward = True
    elif qualifier in _FORWARD_QUALIFIERS:
        backward = False
    else:
        backward = prefer == "past"

    # Strictly before / after today: "tuesday" asked on a Tuesday means the
    # neighbouring one, never today — the user would have said "today".
    delta = (today.weekday() - target) % 7 if backward else (target - today.weekday()) % 7
    delta = delta or 7
    return today - timedelta(days=delta) if backward else today + timedelta(days=delta)


def _resolve_bare_month_day(month: int, day: int, today: date, prefer: str) -> date:
    """Pick the year for a bare MM-DD according to `prefer`.

    - "past"    -> the most recent past occurrence (this year, else last year).
    - "future"  -> the next occurrence (this year, else next year).
    - "nearest" -> whichever occurrence is closest to `today` in either
                   direction.

    Feb-29 in a non-leap year raises ValueError for that candidate year; such
    years are simply skipped rather than crashing the whole resolution."""
    def candidate(year: int) -> Optional[date]:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    if prefer == "future":
        this_year = candidate(today.year)
        if this_year is None or this_year < today:
            return candidate(today.year + 1) or date(today.year, month, day)
        return this_year

    if prefer == "nearest":
        options = [c for c in (candidate(today.year - 1), candidate(today.year), candidate(today.year + 1)) if c]
        return min(options, key=lambda c: abs((c - today).days))

    # "past" (default): this year, unless it hasn't happened yet.
    this_year = candidate(today.year)
    if this_year is None or this_year > today:
        return candidate(today.year - 1) or date(today.year, month, day)
    return this_year


def resolve_date(date_str: str, *, today: Optional[date] = None, prefer: str = "past") -> str:
    """Map a user-supplied date onto a concrete 'YYYY-MM-DD' string.

    - 'today' / 'tomorrow' / 'yesterday' -> relative to `today` (defaults to now)
    - 'next tuesday' / 'last friday' / a bare weekday -> see
      _resolve_relative_day; a bare weekday follows `prefer`
    - 'YYYY-MM-DD'                 -> honored as-is (an explicit year wins)
    - 'MM-DD' / 'M-D' (also '/')   -> a bare month/day, with the year filled in
      per `prefer` ("past" | "future" | "nearest"; see
      _resolve_bare_month_day).

    Anything unparseable is returned unchanged, so callers never crash on odd
    input — their downstream lookup simply won't match it.

    `today` is injectable so timezone-aware callers can pass their own local
    date and tests can pin the result."""
    today = today or datetime.now().date()

    relative = _resolve_relative_day(date_str, today, prefer)
    if relative is not None:
        return relative.isoformat()

    parts = date_str.strip().replace("/", "-").split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        if len(parts) == 2:
            month, day = int(parts[0]), int(parts[1])
            return _resolve_bare_month_day(month, day, today, prefer).isoformat()
    except ValueError:
        pass
    return date_str
