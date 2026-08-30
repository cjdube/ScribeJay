"""Tests for scribejay/sinks/calendar.py — log_calendar_event's source_id
dedupe, and set_event_color, the two writes ScribeJay makes to Google
Calendar.

Mirrors the log_calendar_event slice of LocalLLMAgent's tests/test_calendar.py.
Dropped get_events_by_date (the chat-tool wrapper with weekday-phrase
resolution and result capping — no counterpart here; scribejay/sources/
calendar.py's get_events_in_range is the only reader and stays uncapped,
covered in test_calendar_read.py). set_event_color has no test in the Wren
original either, but it is real, load-bearing logic (scribejay/
calendar_colorizer.py depends on it), so one is added here."""

import pytest

from scribejay.sinks import calendar as cal


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEvents:
    """Records list/insert/patch calls; returns canned payloads."""

    def __init__(self, existing_items=()):
        self.existing_items = list(existing_items)
        self.list_kwargs = []
        self.inserted = []
        self.patched = []

    def list(self, **kwargs):
        self.list_kwargs.append(kwargs)
        return _Exec({"items": self.existing_items})

    def insert(self, calendarId, body):
        self.inserted.append(body)
        return _Exec({"id": "new-event", "htmlLink": "https://cal/new-event"})

    def patch(self, calendarId, eventId, body):
        self.patched.append((eventId, body))
        return _Exec({"id": eventId, **body})


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


def test_log_event_with_source_id_skips_when_already_logged(fake_events):
    fake_events["events"] = events = _FakeEvents(existing_items=[
        {"id": "existing-1", "htmlLink": "https://cal/existing-1"},
    ])

    result = cal.log_calendar_event("Run", "2026-07-10T08:00:00", "2026-07-10T09:00:00",
                                    source_id="strava-42")

    assert result["skipped"] == "event already logged for this source_id"
    assert result["event_id"] == "existing-1"
    assert events.inserted == []  # the duplicate was never created
    assert events.list_kwargs[0]["privateExtendedProperty"] == "source_id=strava-42"


def test_log_event_with_source_id_stamps_the_extended_property(fake_events):
    events = fake_events["events"]

    result = cal.log_calendar_event("Run", "2026-07-10T08:00:00", "2026-07-10T09:00:00",
                                    source_id="strava-42", color_id="4")

    assert result["event_id"] == "new-event"
    assert result["html_link"] == "https://cal/new-event"
    body = events.inserted[0]
    # The property queried by the dedupe lookup must be the one stamped here —
    # this pairing IS the idempotency guarantee.
    assert body["extendedProperties"]["private"]["source_id"] == "strava-42"
    assert body["colorId"] == "4"


def test_log_event_without_source_id_never_queries(fake_events):
    events = fake_events["events"]
    result = cal.log_calendar_event("Lunch", "2026-07-10T12:00:00", "2026-07-10T13:00:00")
    assert result["event_id"] == "new-event"
    assert events.list_kwargs == []  # no pointless dedupe round-trip
    assert "extendedProperties" not in events.inserted[0]


# --- the human "when" echoed back --------------------------------------------
# A write result of two opaque ids gives no evidence the event actually
# exists; the tool states the time it used instead.

def test_log_event_echoes_back_what_it_wrote(fake_events, monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")

    result = cal.log_calendar_event("do yardwork", "2026-08-19T10:00:00",
                                    "2026-08-19T11:00:00")

    assert result["created"] is True
    assert result["summary"] == "do yardwork"
    assert result["when"] == "Wednesday, August 19, 2026, 10:00 AM to 11:00 AM"


def test_when_spells_out_both_days_for_an_overnight_event(fake_events, monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")

    result = cal.log_calendar_event("red-eye", "2026-08-19T22:00:00",
                                    "2026-08-20T06:00:00")

    assert result["when"] == ("Wednesday, August 19, 2026, 10:00 PM to "
                              "Thursday, August 20, 2026, 6:00 AM")


def test_an_unparseable_time_degrades_instead_of_failing_the_write(fake_events):
    # "when" is a display string; a write that Google accepted must not fail on it.
    result = cal.log_calendar_event("odd", "whenever", "later")

    assert result["created"] is True
    assert result["when"] == "whenever to later"


# --- set_event_color ----------------------------------------------------------

def test_set_event_color_patches_only_the_color(fake_events):
    result = cal.set_event_color("evt-1", "4")

    assert result == {"event_id": "evt-1", "color_id": "4", "updated": True}
    event_id, body = fake_events["events"].patched[0]
    assert event_id == "evt-1"
    assert body == {"colorId": "4"}


def test_set_event_color_degrades_on_failure(monkeypatch):
    def _broken(api, version):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(cal, "build_service", _broken)

    result = cal.set_event_color("evt-1", "4")
    assert "error" in result
