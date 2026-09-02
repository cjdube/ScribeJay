"""Tests for scribejay/sources/gmail.py — fetch_sent_metadata, fetch_inbox_metadata and
my_address. The Gmail client is replaced wholesale with a fake, so nothing
here touches the network or a real mailbox.

Mirrors the fetch_sent_metadata + my_address slice of LocalLLMAgent's
tests/test_gmail_read.py — dropped every fixture/test tied to threads,
labels, search, or the history watcher, since scribejay/sources/gmail.py
carries none of that (see its module docstring)."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError

from scribejay.sources import gmail as gmail_source

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
    gmail_source._MY_ADDRESS.clear()
    yield
    gmail_source._MY_ADDRESS.clear()


@pytest.fixture
def gmail(monkeypatch):
    fake = _FakeGmail()
    monkeypatch.setattr(gmail_source, "_service", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# fetch_sent_metadata
# --------------------------------------------------------------------------- #

def test_sent_metadata_returns_a_row_per_message(gmail):
    gmail.messages_by_id = {"s1": _sent()}
    gmail.search_results = ["s1"]
    result = gmail_source.fetch_sent_metadata(_START, _END)
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
    gmail_source.fetch_sent_metadata(_START, _END)
    call = gmail.get_calls[0]
    assert call["format"] == "metadata"
    assert sorted(call["metadataHeaders"]) == ["Cc", "In-Reply-To", "Subject", "To"]


def test_no_row_carries_a_body_or_a_snippet(gmail):
    gmail.messages_by_id = {"s1": _sent()}
    gmail.search_results = ["s1"]
    row = gmail_source.fetch_sent_metadata(_START, _END)["messages"][0]
    assert set(row) == {"message_id", "thread_id", "to", "cc", "subject", "date", "is_reply"}


def test_the_window_is_sent_as_epoch_seconds(gmail):
    # Gmail's after:/before: take whole days in the ACCOUNT's timezone, which is
    # not necessarily this machine's — epoch seconds are the only unambiguous
    # form, so a 00:00-23:59 local day cannot silently slide onto the wrong day.
    gmail.search_results = []
    gmail_source.fetch_sent_metadata(_START, _END)
    query = gmail.list_calls[0]["q"]
    assert query == (f"in:sent after:{int(_START.timestamp())} "
                     f"before:{int(_END.timestamp())}")


def test_only_sent_mail_is_asked_for(gmail):
    gmail.search_results = []
    gmail_source.fetch_sent_metadata(_START, _END)
    assert gmail.list_calls[0]["q"].startswith("in:sent ")


def test_a_reply_is_marked_by_its_threading_header(gmail):
    gmail.messages_by_id = {"s1": _sent(in_reply_to="<abc@acme.com>"),
                            "s2": _sent(message_id="s2", in_reply_to=None,
                                        internal_date="1755000060000")}
    gmail.search_results = ["s1", "s2"]
    rows = gmail_source.fetch_sent_metadata(_START, _END)["messages"]
    assert [r["is_reply"] for r in rows] == [True, False]


def test_a_message_with_no_subject_gets_a_label_not_a_blank(gmail):
    message = _sent()
    message["payload"]["headers"] = [{"name": "To", "value": "kat@vendor.example"}]
    gmail.messages_by_id = {"s1": message}
    gmail.search_results = ["s1"]
    row = gmail_source.fetch_sent_metadata(_START, _END)["messages"][0]
    assert row["subject"] == "(no subject)"
    assert row["cc"] == ""


def test_rows_come_back_oldest_first(gmail):
    gmail.messages_by_id = {
        "s1": _sent(message_id="s1", internal_date="1755009999000"),
        "s2": _sent(message_id="s2", internal_date="1755000000000"),
    }
    gmail.search_results = ["s1", "s2"]
    rows = gmail_source.fetch_sent_metadata(_START, _END)["messages"]
    assert [r["message_id"] for r in rows] == ["s2", "s1"]


def test_one_unreadable_message_does_not_cost_the_day(gmail):
    # The list gives ids; a get can still 404 (deleted between the two calls).
    gmail.messages_by_id = {"s2": _sent(message_id="s2")}
    gmail.search_results = ["gone", "s2"]
    result = gmail_source.fetch_sent_metadata(_START, _END)
    assert result["count"] == 1
    assert result["messages"][0]["message_id"] == "s2"


def test_gmail_being_down_is_an_error_dict_not_a_crash(gmail):
    # AGENTS.md: a failing source degrades to empty for its caller.
    gmail.list_error = _http_error(503, "backend error")
    result = gmail_source.fetch_sent_metadata(_START, _END)
    assert "error" in result
    assert "messages" not in result


def test_a_day_with_no_sent_mail_is_empty_not_an_error(gmail):
    gmail.search_results = []
    assert gmail_source.fetch_sent_metadata(_START, _END) == {"messages": [], "count": 0}


def test_the_result_is_capped(gmail):
    gmail.messages_by_id = {f"s{i}": _sent(message_id=f"s{i}") for i in range(200)}
    gmail.search_results = [f"s{i}" for i in range(200)]
    gmail_source.fetch_sent_metadata(_START, _END)
    assert gmail.list_calls[0]["maxResults"] == gmail_source.SENT_MAX_RESULTS


# --------------------------------------------------------------------------- #
# my_address — identity, not delivery preference.
# --------------------------------------------------------------------------- #

def test_my_address_comes_from_the_gmail_profile(gmail):
    gmail.profile_address = "craig@example.com"
    assert gmail_source.my_address() == "craig@example.com"


def test_my_address_is_only_fetched_once(gmail):
    gmail_source.my_address()
    gmail_source.my_address()
    assert gmail.profile_calls == 1


def test_a_failed_profile_read_is_not_cached(monkeypatch, gmail):
    """Caching "" would pin the failure for the life of the daemon, and every
    later run would quietly treat every message as going to a stranger."""
    monkeypatch.setattr(gmail_source, "_service",
                        lambda: (_ for _ in ()).throw(RuntimeError("gmail down")))
    assert gmail_source.my_address() == ""

    monkeypatch.setattr(gmail_source, "_service", lambda: gmail)
    assert gmail_source.my_address() == "craig@example.com"


# --------------------------------------------------------------------------- #
# fetch_inbox_metadata — the inbound half. Every subject and display name here
# is chosen by a stranger, which is why the header set is pinned as tightly as
# the sent one.
# --------------------------------------------------------------------------- #

def _inbound(message_id="i1", thread_id="T9", sender="Kat <kat@vendor.example>",
             to="craig@example.com", cc="", subject="Proposal",
             internal_date="1755000000000", header_id="<m1@vendor.example>",
             references=None):
    """An arrived message as the metadata format returns it. Same shape as
    _sent(), with From and the threading headers that a sent row never needs."""
    headers = [{"name": "From", "value": sender},
               {"name": "To", "value": to},
               {"name": "Subject", "value": subject},
               {"name": "Message-ID", "value": header_id}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if references:
        headers.append({"name": "References", "value": references})
    return {"id": message_id, "threadId": thread_id, "internalDate": internal_date,
            "payload": {"headers": headers}}


def test_inbox_metadata_returns_a_row_per_message(gmail):
    gmail.messages_by_id = {"i1": _inbound()}
    gmail.search_results = ["i1"]
    result = gmail_source.fetch_inbox_metadata(_START, _END)
    assert result["count"] == 1
    row = result["messages"][0]
    assert row["from"] == "Kat <kat@vendor.example>"
    assert row["subject"] == "Proposal"
    assert row["thread_id"] == "T9"


def test_inbox_never_asks_for_a_body(gmail):
    # The same promise the sent fetcher makes, and it matters more here:
    # asserted on the request, because a row with no body could equally mean
    # a body was fetched and then discarded.
    gmail.messages_by_id = {"i1": _inbound()}
    gmail.search_results = ["i1"]
    gmail_source.fetch_inbox_metadata(_START, _END)
    call = gmail.get_calls[0]
    assert call["format"] == "metadata"
    assert sorted(call["metadataHeaders"]) == [
        "Cc", "From", "Message-ID", "References", "Subject", "To"]


def test_no_inbox_row_carries_a_body_or_a_snippet(gmail):
    gmail.messages_by_id = {"i1": _inbound()}
    gmail.search_results = ["i1"]
    row = gmail_source.fetch_inbox_metadata(_START, _END)["messages"][0]
    assert set(row) == {"message_id", "thread_id", "from", "to", "cc", "subject",
                        "date", "header_id", "references"}


def test_inbox_excludes_what_he_wrote_himself(gmail):
    gmail.search_results = []
    gmail_source.fetch_inbox_metadata(_START, _END)
    query = gmail.list_calls[0]["q"]
    for own in ("-in:sent", "-in:draft", "-in:spam", "-in:trash", "-in:chats"):
        assert own in query


def test_inbox_includes_archived_mail(gmail):
    """The design decision worth pinning. This runs at 5:20 the next morning,
    by which time anything dealt with is often archived — and an archived
    message he never answered is exactly the one worth recording. Scoping to
    in:inbox would drop it."""
    gmail.search_results = []
    gmail_source.fetch_inbox_metadata(_START, _END)
    assert "in:inbox" not in gmail.list_calls[0]["q"]


def test_inbox_leaves_gmails_own_noise_tabs_out(gmail):
    # Gmail already sorts newsletters, per account. A hand-kept sender list
    # would need an entry every time he subscribes to something.
    gmail.search_results = []
    gmail_source.fetch_inbox_metadata(_START, _END)
    query = gmail.list_calls[0]["q"]
    for tab in ("promotions", "social", "updates", "forums"):
        assert f"-category:{tab}" in query


def test_the_inbox_window_is_sent_as_epoch_seconds(gmail):
    gmail.search_results = []
    gmail_source.fetch_inbox_metadata(_START, _END)
    query = gmail.list_calls[0]["q"]
    assert (f"after:{int(_START.timestamp())} "
            f"before:{int(_END.timestamp())}") in query


def test_inbox_rows_come_back_oldest_first(gmail):
    gmail.messages_by_id = {
        "i1": _inbound(message_id="i1", internal_date="1755009999000"),
        "i2": _inbound(message_id="i2", internal_date="1755000000000"),
    }
    gmail.search_results = ["i1", "i2"]
    rows = gmail_source.fetch_inbox_metadata(_START, _END)["messages"]
    assert [r["message_id"] for r in rows] == ["i2", "i1"]


def test_an_inbound_message_with_no_subject_gets_a_label_not_a_blank(gmail):
    message = _inbound()
    message["payload"]["headers"] = [{"name": "From", "value": "kat@vendor.example"}]
    gmail.messages_by_id = {"i1": message}
    gmail.search_results = ["i1"]
    row = gmail_source.fetch_inbox_metadata(_START, _END)["messages"][0]
    assert row["subject"] == "(no subject)"
    assert row["cc"] == ""
    assert row["references"] == ""


def test_one_unreadable_inbound_message_does_not_cost_the_day(gmail):
    gmail.messages_by_id = {"i2": _inbound(message_id="i2")}
    gmail.search_results = ["gone", "i2"]
    result = gmail_source.fetch_inbox_metadata(_START, _END)
    assert result["count"] == 1
    assert result["messages"][0]["message_id"] == "i2"


def test_inbox_gmail_being_down_is_an_error_dict_not_a_crash(gmail):
    gmail.list_error = _http_error(503, "backend error")
    result = gmail_source.fetch_inbox_metadata(_START, _END)
    assert "error" in result
    assert "messages" not in result


def test_a_day_with_no_inbound_mail_is_empty_not_an_error(gmail):
    gmail.search_results = []
    assert gmail_source.fetch_inbox_metadata(_START, _END) == {"messages": [], "count": 0}


def test_the_inbox_result_is_capped(gmail):
    gmail.messages_by_id = {f"i{i}": _inbound(message_id=f"i{i}") for i in range(300)}
    gmail.search_results = [f"i{i}" for i in range(300)]
    gmail_source.fetch_inbox_metadata(_START, _END)
    assert gmail.list_calls[0]["maxResults"] == gmail_source.INBOX_MAX_RESULTS


def test_the_two_fetchers_do_not_share_a_header_set(gmail):
    """A sent row must never carry From, and an inbound row must never be
    fetched without it — the two lists are asked for separately, server-side."""
    gmail.messages_by_id = {"s1": _sent(), "i1": _inbound()}
    gmail.search_results = ["s1"]
    gmail_source.fetch_sent_metadata(_START, _END)
    assert "From" not in gmail.get_calls[0]["metadataHeaders"]

    gmail.get_calls.clear()
    gmail.search_results = ["i1"]
    gmail_source.fetch_inbox_metadata(_START, _END)
    assert "From" in gmail.get_calls[0]["metadataHeaders"]
