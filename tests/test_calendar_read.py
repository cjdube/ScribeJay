"""Tests for scribejay/sources/calendar.py — get_events_in_range, the
uncapped event reader calendar_colorizer and daily_chrome_learnings depend
on.

Mirrors the get_events_in_range slice of LocalLLMAgent's tests/test_calendar.py.
Dropped get_events_by_date and everything about capping/weekday-phrase
resolution/the tool-result char cap — that's the chat-tool wrapper, which has
no counterpart here (ScribeJay tasks call get_events_in_range directly and
need every event whole)."""

import pytest

from scribejay.sources import calendar as cal


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEvents:
    def __init__(self, existing_items=()):
        self.existing_items = list(existing_items)

    def list(self, **kwargs):
        return _Exec({"items": self.existing_items})


class _FakeService:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


@pytest.fixture
def fake_events(monkeypatch):
    holder = {"events": _FakeEvents()}
    monkeypatch.setattr(cal, "build_service",
                        lambda api, version: _FakeService(holder["events"]))
    return holder


def test_events_in_range_surfaces_source_id(fake_events):
    # calendar_colorizer depends on this to leave the session blocks it must
    # not recolor alone.
    fake_events["events"] = _FakeEvents(existing_items=[
        {"id": "e1", "summary": "AI session — added the digest",
         "start": {"dateTime": "2026-07-10T08:00:00-04:00"},
         "end": {"dateTime": "2026-07-10T09:00:00-04:00"},
         "extendedProperties": {"private": {"source_id": "claude-time:2026-07-10:0800"}}},
        {"id": "e2", "summary": "Dentist",
         "start": {"dateTime": "2026-07-10T10:00:00-04:00"},
         "end": {"dateTime": "2026-07-10T11:00:00-04:00"}},
    ])

    result = cal.get_events_in_range("2026-07-10T00:00:00", "2026-07-10T23:59:59")

    assert [e["source_id"] for e in result["events"]] == [
        "claude-time:2026-07-10:0800", None,
    ]


def test_get_events_in_range_is_left_uncapped(fake_events):
    events = [{"id": f"evt-{i:04d}", "summary": "M" * 40,
              "start": {"dateTime": f"2026-08-{(i % 28) + 1:02d}T09:00:00-04:00"},
              "end": {"dateTime": f"2026-08-{(i % 28) + 1:02d}T10:00:00-04:00"},
              "colorId": "4", "status": "confirmed"}
             for i in range(200)]
    fake_events["events"] = _FakeEvents(existing_items=events)

    result = cal.get_events_in_range("2026-08-01T00:00:00", "2026-08-28T23:59:59")

    assert result["event_count"] == 200 and len(result["events"]) == 200
    assert "colorId" in result["events"][0] and "source_id" in result["events"][0]


def test_all_day_events_fall_back_to_the_date_field(fake_events):
    fake_events["events"] = _FakeEvents(existing_items=[
        {"id": "e1", "summary": "Vacation", "start": {"date": "2026-08-10"},
         "end": {"date": "2026-08-12"}},
    ])

    result = cal.get_events_in_range("2026-08-01T00:00:00", "2026-08-28T23:59:59")

    assert result["events"][0]["start"] == "2026-08-10"
    assert result["events"][0]["end"] == "2026-08-12"


def test_a_failure_degrades_to_an_error_dict(monkeypatch):
    def _broken(api, version):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(cal, "build_service", _broken)

    result = cal.get_events_in_range("2026-08-01T00:00:00", "2026-08-28T23:59:59")
    assert "error" in result
