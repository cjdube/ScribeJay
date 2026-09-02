"""Tests for scribejay/daily_correspondence.py — that main() reads sent metadata
and persists one Correspondence page per day. Every collaborator is stubbed
("monkeypatched" — swapped for a fake for the length of one test); nothing here
touches Gmail, the vault, or the phone."""

import sys

import pytest

from _helpers import LINE_RE, is_run_start, is_run_success
from scribejay import daily_correspondence as dc

ME = "me@example.com"


def _messages(capsys) -> list:
    """The message halves of the captured log lines, split the way ScribeJay's
    own logging convention expects — so the boundary assertions below stay in
    sync with what a real dashboard would parse."""
    out = []
    for line in capsys.readouterr().out.splitlines():
        m = LINE_RE.match(line)
        if m:
            out.append(m.group(3))
    return out


@pytest.fixture
def stubbed_run(monkeypatch):
    """A happy path: one real message sent yesterday, to one real person."""
    seen = {"persists": [], "windows": []}

    def _fetch(start, end, *a, **k):
        seen["windows"].append((start, end))
        return {"count": 1, "messages": [
            {"message_id": "m1", "thread_id": "T", "to": "Kat <kat@vendor.example>", "cc": "",
             "subject": "Re: Catch Up", "date": "2026-08-21 10:00", "is_reply": True}]}

    monkeypatch.setattr(dc, "fetch_sent_metadata", _fetch)
    # The inbound half, stubbed empty by default. Left unstubbed it reaches the
    # real Gmail API — and because the fetcher degrades to {"error": ...} the
    # test would still pass, which is what tests/conftest.py's Gmail guard now
    # refuses. Tests that care about arrived mail re-patch this.
    monkeypatch.setattr(dc, "fetch_inbox_metadata",
                        lambda *a, **k: {"count": 0, "messages": []})
    monkeypatch.setattr(dc, "my_address", lambda: ME)
    monkeypatch.setattr(dc, "persist_or_email",
                        lambda content, prefix, day, subject, task_name, logger, directory=None:
                        seen["persists"].append((prefix, day, content, directory)) or {"written": True})
    monkeypatch.setattr(dc, "notify_failure", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["daily_correspondence"])
    return seen


def test_happy_path_writes_one_page(stubbed_run):
    assert dc.main() == 0
    assert len(stubbed_run["persists"]) == 1
    prefix, _day, content, _dir = stubbed_run["persists"][0]
    assert prefix == "Correspondence"
    assert "Catch Up" in content
    assert "Kat" in content


def test_the_page_is_written_outside_the_vault_ingest_queue(stubbed_run, monkeypatch):
    # Option B, asserted at the call: these pages name people, so they must not
    # land in LEARNINGS_DIR where ObsidianWikiAgent would make wiki pages of them.
    monkeypatch.setenv("CORRESPONDENCE_DIR", "/tmp/corr-target")
    assert dc.main() == 0
    assert str(stubbed_run["persists"][0][3]) == "/tmp/corr-target"


def test_a_day_of_only_self_addressed_mail_writes_nothing(stubbed_run, monkeypatch):
    # A daily digest email to himself is the common case for a quiet day.
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"count": 1, "messages": [
        {"message_id": "m1", "thread_id": "T", "to": ME, "cc": "",
         "subject": "Morning Brief - 2026-08-21", "date": "2026-08-21 06:00", "is_reply": False}]})
    assert dc.main() == 0
    assert stubbed_run["persists"] == []


def test_a_quiet_day_still_logs_a_finished_run(stubbed_run, monkeypatch, capsys):
    # A run that starts and never completes would read as still running forever
    # if anything ever parses this log.
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"count": 0, "messages": []})
    assert dc.main() == 0
    msgs = _messages(capsys)
    assert any(is_run_start(m) for m in msgs)
    assert any(is_run_success(m) for m in msgs)


def test_gmail_failing_is_a_warning_not_a_failed_run(stubbed_run, monkeypatch, capsys):
    # AGENTS.md: a dead source reads as an empty day — but never silently, or a
    # missing page looks like a quiet day instead of a broken one.
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"error": "gmail 503"})
    assert dc.main() == 0
    assert stubbed_run["persists"] == []
    msgs = _messages(capsys)
    assert any("gmail 503" in m for m in msgs)
    assert any(is_run_success(m) for m in msgs)


def test_not_knowing_his_own_address_is_a_hard_failure(stubbed_run, monkeypatch):
    # Every rule in the page is "everyone who is not him". With no address, every
    # message looks like it went to a stranger — worse than writing nothing.
    alerts = []
    monkeypatch.setattr(dc, "my_address", lambda: "")
    monkeypatch.setattr(dc, "notify_failure", lambda name, detail, logger=None: alerts.append(str(detail)))
    assert dc.main() == 1
    assert stubbed_run["persists"] == []
    assert alerts


def test_gmail_is_never_asked_before_we_know_who_he_is(stubbed_run, monkeypatch):
    monkeypatch.setattr(dc, "my_address", lambda: "")
    assert dc.main() == 1
    assert stubbed_run["windows"] == []


def test_date_writes_the_day_asked_for(stubbed_run, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_correspondence", "--date", "2026-08-21"])
    assert dc.main() == 0
    _prefix, day, content, _dir = stubbed_run["persists"][0]
    assert str(day) == "2026-08-21"
    assert "August 21, 2026" in content


def test_the_day_window_covers_the_whole_local_day(stubbed_run, monkeypatch):
    # Gmail's own after:/before: take whole days in the ACCOUNT's timezone, which
    # is why the fetch takes real datetimes and turns them into epoch seconds.
    monkeypatch.setattr(sys, "argv", ["daily_correspondence", "--date", "2026-08-21"])
    dc.main()
    start, end = stubbed_run["windows"][0]
    assert (start.hour, start.minute) == (0, 0)
    assert end.hour == 23
    assert start.tzinfo is not None and end.tzinfo is not None


def test_backfill_writes_a_page_per_day(stubbed_run, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_correspondence", "--backfill", "3"])
    assert dc.main() == 0
    days = [p[1] for p in stubbed_run["persists"]]
    assert len(days) == 3
    assert days == sorted(days)  # oldest first, so the log reads chronologically


def test_backfill_is_one_run_in_the_dashboard_not_three(stubbed_run, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["daily_correspondence", "--backfill", "3"])
    dc.main()
    msgs = _messages(capsys)
    assert sum(1 for m in msgs if is_run_start(m)) == 1
    assert sum(1 for m in msgs if is_run_success(m)) == 1


def test_one_bad_day_does_not_stop_the_backfill(stubbed_run, monkeypatch):
    calls = {"n": 0}

    def _fetch(start, end, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"error": "gmail 503"}
        return {"count": 1, "messages": [
            {"message_id": "m", "thread_id": f"T{calls['n']}", "to": "kat@vendor.example", "cc": "",
             "subject": "hi", "date": "2026-08-21 10:00", "is_reply": False}]}

    monkeypatch.setattr(dc, "fetch_sent_metadata", _fetch)
    monkeypatch.setattr(sys, "argv", ["daily_correspondence", "--backfill", "3"])
    assert dc.main() == 0
    assert len(stubbed_run["persists"]) == 2


def test_a_write_failure_is_a_failed_run(stubbed_run, monkeypatch):
    alerts = []
    monkeypatch.setattr(dc, "persist_or_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vault gone")))
    monkeypatch.setattr(dc, "notify_failure", lambda name, detail, logger=None: alerts.append(str(detail)))
    assert dc.main() == 1
    assert any("vault gone" in a for a in alerts)


# --------------------------------------------------------------------------- #
# The inbound half, and the order the two halves and the store are touched in.
# --------------------------------------------------------------------------- #

def _seed_quiet_thread() -> None:
    """One month-old thread he never answered, already in the store."""
    from scribejay import correspondence as co
    from scribejay.core.store import atomic_write_json
    atomic_write_json(co.store_path(), {"threads": {"OLD": {
        "subject": "Budget question", "people": {"max@x.example": "Max"},
        "first_seen": "2026-08-01 09:00", "last_inbound": "2026-08-01 09:00",
        "last_outbound": ""}}})


def _inbox(**over):
    row = {"message_id": "i1", "thread_id": "IN", "from": "Sam <sam@x.example>",
           "to": ME, "cc": "", "subject": "Question for you",
           "date": "2026-08-21 11:00", "header_id": "<h1>", "references": ""}
    row.update(over)
    return row


def test_arrived_mail_reaches_the_page(stubbed_run, monkeypatch):
    monkeypatch.setattr(dc, "fetch_inbox_metadata",
                        lambda *a, **k: {"count": 1, "messages": [_inbox()]})
    assert dc.main() == 0
    assert "Question for you" in stubbed_run["persists"][0][2]


def test_a_day_with_only_arrived_mail_still_writes_a_page(stubbed_run, monkeypatch):
    # The whole reason for reading the inbox: a day he answered nobody used to
    # produce no page at all, which is the day worth seeing.
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"count": 0, "messages": []})
    monkeypatch.setattr(dc, "fetch_inbox_metadata",
                        lambda *a, **k: {"count": 1, "messages": [_inbox()]})
    assert dc.main() == 0
    assert len(stubbed_run["persists"]) == 1


def test_one_failing_half_still_writes_the_other(stubbed_run, monkeypatch, capsys):
    monkeypatch.setattr(dc, "fetch_inbox_metadata", lambda *a, **k: {"error": "boom"})
    assert dc.main() == 0
    assert len(stubbed_run["persists"]) == 1
    assert any("fetch_inbox_metadata failed" in m for m in _messages(capsys))


def test_both_halves_failing_writes_nothing(stubbed_run, monkeypatch):
    """A page built from two dead sources reads as a day he spoke to nobody.
    That is a different day, and the record must not claim it.

    The store is seeded on purpose. With both fetchers dead but a quiet thread
    waiting, there IS something to render — so this is the only arrangement
    where treating a failure as an empty day actually produces a false page,
    and the only one that can prove the distinction is real."""
    _seed_quiet_thread()
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"error": "boom"})
    monkeypatch.setattr(dc, "fetch_inbox_metadata", lambda *a, **k: {"error": "boom"})
    assert dc.main() == 0
    assert stubbed_run["persists"] == []


def test_a_failing_half_is_not_confused_with_an_empty_one(stubbed_run, monkeypatch, capsys):
    # Both halves answer; one is simply an empty day. No warning belongs here.
    monkeypatch.setattr(dc, "fetch_inbox_metadata", lambda *a, **k: {"count": 0, "messages": []})
    assert dc.main() == 0
    assert not any("failed" in m for m in _messages(capsys))


def test_the_run_still_ends_cleanly_when_a_half_fails(stubbed_run, monkeypatch, capsys):
    # The dashboard reads run history from these lines, not from exit codes.
    monkeypatch.setattr(dc, "fetch_inbox_metadata", lambda *a, **k: {"error": "boom"})
    assert dc.main() == 0
    messages = _messages(capsys)
    assert any(is_run_start(m) for m in messages)
    assert any(is_run_success(m) for m in messages)


def test_the_day_is_remembered_after_the_page_is_written(stubbed_run):
    from scribejay import correspondence as co
    assert dc.main() == 0
    assert "T" in co.load_threads()


def test_the_page_describes_the_state_before_this_run(stubbed_run, monkeypatch):
    """Order matters: fold today into the store first and every thread reads as
    already known, so nothing is ever a first contact again."""
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"count": 1, "messages": [
        {"message_id": "m1", "thread_id": "BRAND-NEW", "to": "New <new@x.example>",
         "cc": "", "subject": "Hello", "date": "2026-08-21 10:00", "is_reply": False}]})
    assert dc.main() == 0
    assert "*(first contact)*" in stubbed_run["persists"][0][2]


def test_a_thread_that_went_quiet_pulls_a_page_on_an_otherwise_silent_day(
        stubbed_run, monkeypatch):
    _seed_quiet_thread()
    monkeypatch.setattr(dc, "fetch_sent_metadata", lambda *a, **k: {"count": 0, "messages": []})
    assert dc.main() == 0
    assert "Budget question" in stubbed_run["persists"][0][2]
