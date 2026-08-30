"""Tests for scribejay/sources/gmail_sent.py — fetch_sent_metadata and
my_address. The Gmail client is replaced wholesale with a fake, so nothing
here touches the network or a real mailbox.

Mirrors the fetch_sent_metadata + my_address slice of LocalLLMAgent's
tests/test_gmail_read.py — dropped every fixture/test tied to threads,
labels, search, or the history watcher, since scribejay/sources/gmail_sent.py
carries none of that (see its module docstring)."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError

from scribejay.sources import gmail_sent

_TZ = ZoneInfo("America/New_York")
_START = datetime(2026, 8, 21, tzinfo=_TZ)
_END = datetime(2026, 8, 21, 23, 59, 59, tzinfo=_TZ)


def _sent(message_id="s1", thread_id="T1", to="Kat <kat@vendor.example>", cc="",
          subject="Re: Catch Up", internal_date="1755000000000", in_reply_to=None):
    """A SENT message as the metadata format returns it: headers only, and the
    payload carries no parts at all — that is what makes it cheap."""
    headers = [{"name": "To", "value": to}, {"name": "Subject", "value": subject}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if in_reply_to:
        headers.append({"name": "In-Reply-To", "value": in_reply_to})
    return {"id": message_id, "threadId": thread_id, "internalDate": internal_date,
            "payload": {"headers": headers}}


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _FakeResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "error"


def _http_error(status, message):
    return HttpError(_FakeResponse(status), json.dumps({"error": message}).encode())


class _FakeMessages:
    def __init__(self, gmail):
        self.gmail = gmail

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        self.gmail.get_calls.append({"id": id, "format": format,
                                     "metadataHeaders": metadataHeaders})
        message = self.gmail.messages_by_id.get(id)
        if message is None:
            return _Exec(_http_error(404, "Not Found"))
        return _Exec(message)

    def list(self, userId=None, q=None, maxResults=None):
        self.gmail.list_calls.append({"q": q, "maxResults": maxResults})
        if self.gmail.list_error is not None:
            return _Exec(self.gmail.list_error)
        ids = self.gmail.search_results[:maxResults]
        return _Exec({"messages": [{"id": i} for i in ids]})


class _FakeGmail:
    """Just enough of the Gmail client for these tests."""

    def __init__(self, profile_address="craig@example.com"):
        self.profile_address = profile_address
        self.profile_calls = 0
        self.messages_by_id = {}
        self.search_results = []
        self.get_calls = []
        self.list_calls = []
        self.list_error = None

    def users(self):
        return self

    def messages(self):
        return _FakeMessages(self)

    def getProfile(self, userId=None):
        self.profile_calls += 1
        return _Exec({"emailAddress": self.profile_address})


@pytest.fixture(autouse=True)
def _clear_address_cache():
    """my_address() caches for the life of the process, so one test's fake
    address would otherwise answer every later test."""
    gmail_sent._MY_ADDRESS.clear()
    yield
    gmail_sent._MY_ADDRESS.clear()


@pytest.fixture
def gmail(monkeypatch):
    fake = _FakeGmail()
    monkeypatch.setattr(gmail_sent, "_service", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# fetch_sent_metadata
# --------------------------------------------------------------------------- #

def test_sent_metadata_returns_a_row_per_message(gmail):
    gmail.messages_by_id = {"s1": _sent()}
    gmail.search_results = ["s1"]
    result = gmail_sent.fetch_sent_metadata(_START, _END)
    assert result["count"] == 1
    row = result["messages"][0]
    assert row["to"] == "Kat <kat@vendor.example>"
    assert row["subject"] == "Re: Catch Up"
    assert row["thread_id"] == "T1"


def test_no_body_is_ever_requested(gmail):
    # The promise of the feature. Asserted on the request, not the result: a row
    # with no body could equally mean a body was fetched and discarded.
    gmail.messages_by_id = {"s1": _sent()}
    gmail.search_results = ["s1"]
    gmail_sent.fetch_sent_metadata(_START, _END)
    call = gmail.get_calls[0]
    assert call["format"] == "metadata"
    assert sorted(call["metadataHeaders"]) == ["Cc", "In-Reply-To", "Subject", "To"]


def test_no_row_carries_a_body_or_a_snippet(gmail):
    gmail.messages_by_id = {"s1": _sent()}
    gmail.search_results = ["s1"]
    row = gmail_sent.fetch_sent_metadata(_START, _END)["messages"][0]
    assert set(row) == {"message_id", "thread_id", "to", "cc", "subject", "date", "is_reply"}


def test_the_window_is_sent_as_epoch_seconds(gmail):
    # Gmail's after:/before: take whole days in the ACCOUNT's timezone, which is
    # not necessarily this machine's — epoch seconds are the only unambiguous
    # form, so a 00:00-23:59 local day cannot silently slide onto the wrong day.
    gmail.search_results = []
    gmail_sent.fetch_sent_metadata(_START, _END)
    query = gmail.list_calls[0]["q"]
    assert query == (f"in:sent after:{int(_START.timestamp())} "
                     f"before:{int(_END.timestamp())}")


def test_only_sent_mail_is_asked_for(gmail):
    gmail.search_results = []
    gmail_sent.fetch_sent_metadata(_START, _END)
    assert gmail.list_calls[0]["q"].startswith("in:sent ")


def test_a_reply_is_marked_by_its_threading_header(gmail):
    gmail.messages_by_id = {"s1": _sent(in_reply_to="<abc@acme.com>"),
                            "s2": _sent(message_id="s2", in_reply_to=None,
                                        internal_date="1755000060000")}
    gmail.search_results = ["s1", "s2"]
    rows = gmail_sent.fetch_sent_metadata(_START, _END)["messages"]
    assert [r["is_reply"] for r in rows] == [True, False]


def test_a_message_with_no_subject_gets_a_label_not_a_blank(gmail):
    message = _sent()
    message["payload"]["headers"] = [{"name": "To", "value": "kat@vendor.example"}]
    gmail.messages_by_id = {"s1": message}
    gmail.search_results = ["s1"]
    row = gmail_sent.fetch_sent_metadata(_START, _END)["messages"][0]
    assert row["subject"] == "(no subject)"
    assert row["cc"] == ""


def test_rows_come_back_oldest_first(gmail):
    gmail.messages_by_id = {
        "s1": _sent(message_id="s1", internal_date="1755009999000"),
        "s2": _sent(message_id="s2", internal_date="1755000000000"),
    }
    gmail.search_results = ["s1", "s2"]
    rows = gmail_sent.fetch_sent_metadata(_START, _END)["messages"]
    assert [r["message_id"] for r in rows] == ["s2", "s1"]


def test_one_unreadable_message_does_not_cost_the_day(gmail):
    # The list gives ids; a get can still 404 (deleted between the two calls).
    gmail.messages_by_id = {"s2": _sent(message_id="s2")}
    gmail.search_results = ["gone", "s2"]
    result = gmail_sent.fetch_sent_metadata(_START, _END)
    assert result["count"] == 1
    assert result["messages"][0]["message_id"] == "s2"


def test_gmail_being_down_is_an_error_dict_not_a_crash(gmail):
    # AGENTS.md: a failing source degrades to empty for its caller.
    gmail.list_error = _http_error(503, "backend error")
    result = gmail_sent.fetch_sent_metadata(_START, _END)
    assert "error" in result
    assert "messages" not in result


def test_a_day_with_no_sent_mail_is_empty_not_an_error(gmail):
    gmail.search_results = []
    assert gmail_sent.fetch_sent_metadata(_START, _END) == {"messages": [], "count": 0}


def test_the_result_is_capped(gmail):
    gmail.messages_by_id = {f"s{i}": _sent(message_id=f"s{i}") for i in range(200)}
    gmail.search_results = [f"s{i}" for i in range(200)]
    gmail_sent.fetch_sent_metadata(_START, _END)
    assert gmail.list_calls[0]["maxResults"] == gmail_sent.SENT_MAX_RESULTS


# --------------------------------------------------------------------------- #
# my_address — identity, not delivery preference.
# --------------------------------------------------------------------------- #

def test_my_address_comes_from_the_gmail_profile(gmail):
    gmail.profile_address = "craig@example.com"
    assert gmail_sent.my_address() == "craig@example.com"


def test_my_address_is_only_fetched_once(gmail):
    gmail_sent.my_address()
    gmail_sent.my_address()
    assert gmail.profile_calls == 1


def test_a_failed_profile_read_is_not_cached(monkeypatch, gmail):
    """Caching "" would pin the failure for the life of the daemon, and every
    later run would quietly treat every message as going to a stranger."""
    monkeypatch.setattr(gmail_sent, "_service",
                        lambda: (_ for _ in ()).throw(RuntimeError("gmail down")))
    assert gmail_sent.my_address() == ""

    monkeypatch.setattr(gmail_sent, "_service", lambda: gmail)
    assert gmail_sent.my_address() == "craig@example.com"
