"""Tests for scribejay/core/dates.py — the shared, past-biased date resolver
and the prior-day range math.

`today` is pinned throughout so the MM-DD past/future boundary is deterministic.

Mirrors the resolve_date + prior_day slice of LocalLLMAgent's tests/test_dates.py
and tests/test_activity_log.py — dropped resolve_reminder_time and the
DATE_ARG_GUIDANCE/REMINDER_WHEN_GUIDANCE prompt strings, since those exist to
steer a tool-calling model's argument choices and ScribeJay never calls tools.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from scribejay.core.dates import local_timezone, prior_day, resolve_date

TODAY = date(2026, 7, 7)  # a Tuesday
_TZ = ZoneInfo("America/New_York")


def test_today():
    assert resolve_date("today", today=TODAY) == "2026-07-07"


def test_today_is_case_and_space_insensitive():
    assert resolve_date("  Today ", today=TODAY) == "2026-07-07"


def test_yesterday():
    assert resolve_date("yesterday", today=TODAY) == "2026-07-06"


def test_yesterday_crosses_month_boundary():
    assert resolve_date("yesterday", today=date(2026, 8, 1)) == "2026-07-31"


def test_mmdd_in_the_past_this_year_keeps_current_year():
    assert resolve_date("07-05", today=TODAY) == "2026-07-05"


def test_mmdd_today_keeps_current_year():
    # Boundary: the candidate equals today, which is not strictly future.
    assert resolve_date("07-07", today=TODAY) == "2026-07-07"


def test_mmdd_in_the_future_rolls_back_to_previous_year():
    # Past-biased: July 10th asked on July 7th resolves to *last* year.
    assert resolve_date("07-10", today=TODAY) == "2025-07-10"


def test_single_digit_month_and_day():
    assert resolve_date("7-5", today=TODAY) == "2026-07-05"


def test_slash_separator_is_normalized():
    assert resolve_date("07/05", today=TODAY) == "2026-07-05"


def test_full_iso_is_honored_even_when_future():
    # An explicit year always wins — it is never rolled back.
    assert resolve_date("2027-01-15", today=TODAY) == "2027-01-15"


def test_full_iso_in_the_past_is_honored():
    assert resolve_date("2020-02-29", today=TODAY) == "2020-02-29"


def test_non_numeric_parts_are_returned_unchanged():
    assert resolve_date("not-a-date", today=TODAY) == "not-a-date"


def test_impossible_calendar_date_is_returned_unchanged():
    # date(2026, 2, 30) raises ValueError -> passthrough, never a crash.
    assert resolve_date("02-30", today=TODAY) == "02-30"


def test_impossible_month_is_returned_unchanged():
    assert resolve_date("13-01", today=TODAY) == "13-01"


# --- prefer= behavior -------------------------------------------------------

def test_prefer_defaults_to_past():
    # Explicit "past" matches the default and the original behavior.
    assert resolve_date("07-10", today=TODAY, prefer="past") == "2025-07-10"


def test_prefer_nearest_keeps_this_year_for_near_future():
    assert resolve_date("07-10", today=TODAY, prefer="nearest") == "2026-07-10"


def test_prefer_nearest_keeps_this_year_for_near_past():
    assert resolve_date("07-05", today=TODAY, prefer="nearest") == "2026-07-05"


def test_prefer_nearest_rolls_back_when_last_year_is_closer():
    assert resolve_date("06-28", today=TODAY, prefer="nearest") == "2026-06-28"


def test_prefer_nearest_rolls_forward_when_next_year_is_closer():
    # January 1st asked on December 1st: next year's Jan 1 (31 days ahead) is
    # nearer than this year's Jan 1 (~334 days back).
    assert resolve_date("01-01", today=date(2026, 12, 1), prefer="nearest") == "2027-01-01"


def test_prefer_future_rolls_forward_for_past_day():
    # A day already gone this year resolves to next year under "future".
    assert resolve_date("07-05", today=TODAY, prefer="future") == "2027-07-05"


def test_prefer_future_keeps_this_year_for_upcoming_day():
    assert resolve_date("07-10", today=TODAY, prefer="future") == "2026-07-10"


def test_prefer_nearest_still_passes_through_impossible_date():
    assert resolve_date("02-30", today=TODAY, prefer="nearest") == "02-30"


# --- relative days -----------------------------------------------------------
#
# Weekday arithmetic lives here rather than in the model: asked for "next
# tuesday", a model can answer with the wrong weekday and report it as fact.

def test_the_reported_bug_next_tuesday_from_a_friday():
    assert resolve_date("next tuesday", today=date(2026, 8, 14), prefer="nearest") == "2026-08-18"


def test_tomorrow():
    assert resolve_date("tomorrow", today=TODAY) == "2026-07-08"


def test_next_weekday_is_the_next_one_after_today():
    # TODAY is a Tuesday: "next tuesday" is a week out, never today.
    assert resolve_date("next tuesday", today=TODAY) == "2026-07-14"
    assert resolve_date("next friday", today=TODAY) == "2026-07-10"


def test_next_weekday_asked_the_day_before_means_tomorrow():
    # Monday 2026-08-17 -> the very next Tuesday, not the following week's.
    assert resolve_date("next tuesday", today=date(2026, 8, 17)) == "2026-08-18"


def test_last_weekday_looks_back():
    assert resolve_date("last tuesday", today=TODAY) == "2026-06-30"
    assert resolve_date("last friday", today=TODAY) == "2026-07-03"


def test_bare_weekday_follows_prefer():
    # A bare weekday has no direction of its own, so the caller's bias decides.
    assert resolve_date("tuesday", today=TODAY, prefer="past") == "2026-06-30"
    assert resolve_date("tuesday", today=TODAY, prefer="nearest") == "2026-07-14"
    assert resolve_date("tuesday", today=TODAY, prefer="future") == "2026-07-14"


def test_explicit_qualifier_beats_prefer():
    assert resolve_date("next tuesday", today=TODAY, prefer="past") == "2026-07-14"
    assert resolve_date("last tuesday", today=TODAY, prefer="future") == "2026-06-30"


def test_weekday_phrasing_variants():
    for phrase in ("Next Tuesday", "  next   tuesday ", "on tuesday", "this tue",
                   "coming tues", "the next tuesday", "the following tuesday"):
        assert resolve_date(phrase, today=TODAY, prefer="nearest") == "2026-07-14"


def test_unrecognised_day_phrase_is_returned_unchanged():
    # Falls through to the numeric parsing and then to passthrough — the caller
    # degrades rather than resolving to a plausible wrong day.
    assert resolve_date("monday morning", today=TODAY) == "monday morning"
    assert resolve_date("sometime next week", today=TODAY) == "sometime next week"


# --- prior_day ---------------------------------------------------------------

def test_prior_day_returns_yesterday_full_span():
    # Run Monday 2026-07-13 05:00 (the launchd shape): covers all of Sunday 07-12.
    start, end, day = prior_day(now=datetime(2026, 7, 13, 5, 0, tzinfo=_TZ))
    assert day == date(2026, 7, 12)
    assert (start.year, start.month, start.day) == (2026, 7, 12)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)


def test_prior_day_crosses_month_boundary():
    start, end, day = prior_day(now=datetime(2026, 8, 1, 5, 0, tzinfo=_TZ))
    assert day == date(2026, 7, 31)


def test_local_timezone_honors_override(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    assert local_timezone() == "America/Chicago"
