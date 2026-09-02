"""Tests for scribejay/correspondence.py — the noise filter, the thread grouping
and the page rendering. All three are pure functions over metadata rows; nothing
here reaches Gmail.

The fixtures are built from real captured headers (a fortnight of the user's own
SENT mail, August 2026), because every one of these rules exists to handle
something that mailbox actually did: a daily digest email to himself, a
customer.io unsubscribe, and one person arriving both named and bare on the
same thread."""

import json
from datetime import date, timedelta

import pytest

from scribejay import correspondence as co

ME = "me@example.com"


def _row(to, subject, cc="", is_reply=False, thread="t1", date="2026-08-21 10:00"):
    return {"message_id": f"m{subject}", "thread_id": thread, "to": to, "cc": cc,
            "subject": subject, "date": date, "is_reply": is_reply}


class _Recorder:
    def __init__(self):
        self.infos = []

    def info(self, msg):
        self.infos.append(msg)


# --------------------------------------------------------------------------- #
# filter_noise
# --------------------------------------------------------------------------- #

def test_mail_to_himself_is_not_correspondence():
    # His own digest emails to himself. 4 of 10 sent messages in the sample
    # fortnight, which is why this rule comes first.
    rows = [_row(ME, "Morning Brief - 2026-08-23"),
            _row("Diego M. Oppenheimer <diego@meetup.example>", "local agent chat - thanks!")]
    kept = co.filter_noise(rows, ME)
    assert [r["subject"] for r in kept] == ["local agent chat - thanks!"]


def test_a_thread_he_is_cc_d_on_still_counts():
    # Self-addressed means EVERY recipient is him. Being cc'd alongside a real
    # person is an ordinary message, and dropping it would lose the conversation.
    rows = [_row("gavin.lawton@client.example", "Re: Catch Up", cc=ME, is_reply=True)]
    assert len(co.filter_noise(rows, ME)) == 1


def test_machine_subjects_are_dropped():
    # The unsubscribe that made the "no real address" shortcut unusable: its
    # recipient IS a valid address (...@unsubscribe2.customer.io), so only the
    # subject separates it from real mail.
    rows = [_row("32.MRTVIMLSO53UYQ=@unsubscribe2.customer.io", "unsubscribe")]
    assert co.filter_noise(rows, ME) == []


def test_the_subject_match_is_exact_not_a_substring():
    # A real message about unsubscribing from something must survive.
    rows = [_row("kat@vendor.example", "How do I unsubscribe from your newsletter?")]
    assert len(co.filter_noise(rows, ME)) == 1


def test_a_message_with_no_recipient_is_dropped():
    assert co.filter_noise([_row("", "(no subject)")], ME) == []


def test_the_filter_is_case_insensitive_about_his_own_address():
    assert co.filter_noise([_row("Me@Example.com", "Morning Brief")], ME) == []


def test_filtering_says_how_much_it_dropped():
    logger = _Recorder()
    co.filter_noise([_row(ME, "Morning Brief"), _row("a@b.com", "hi")], ME, logger=logger)
    assert any("filtered 1 of 2" in m for m in logger.infos), logger.infos


def test_nothing_dropped_is_silent():
    logger = _Recorder()
    co.filter_noise([_row("a@b.com", "hi")], ME, logger=logger)
    assert logger.infos == []


# --------------------------------------------------------------------------- #
# group_threads
# --------------------------------------------------------------------------- #

def test_a_conversation_is_one_row_not_four():
    rows = [_row("joe@meetup.example", "Re: Chat about local-first agents?",
                 cc="diego@meetup.example", is_reply=True, thread="T"),
            _row("howie@howie.example", "Re: Chat about local-first agents?",
                 cc="diego@meetup.example", is_reply=True, thread="T")]
    threads = co.group_threads(rows, ME)
    assert len(threads) == 1
    assert threads[0]["messages"] == 2


def test_one_person_named_on_one_message_and_bare_on_another_is_listed_once():
    # Real bug from the August 21 thread: Diego arrived as a display name on one
    # message and as a bare address on another, and was listed twice.
    rows = [_row("joe@meetup.example", "Re: Chat", cc="diego@meetup.example",
                 is_reply=True, thread="T"),
            _row("howie@howie.example", "Re: Chat",
                 cc='"Diego M. Oppenheimer" <diego@meetup.example>',
                 is_reply=True, thread="T")]
    people = list(co.group_threads(rows, ME)[0]["people"].values())
    assert people.count("Diego M. Oppenheimer") == 1
    assert "diego@meetup.example" not in people   # the name won, not the address


@pytest.mark.parametrize("order", ["named_first", "bare_first"])
def test_the_display_name_wins_whichever_message_carried_it(order):
    named = _row("a@b.com", "Re: X", cc='"Real Name" <c@d.com>', is_reply=True, thread="T")
    bare = _row("a@b.com", "Re: X", cc="c@d.com", is_reply=True, thread="T")
    rows = [named, bare] if order == "named_first" else [bare, named]
    assert co.group_threads(rows, ME)[0]["people"]["c@d.com"] == "Real Name"


def test_he_is_never_listed_among_the_people():
    rows = [_row("gavin.lawton@client.example", "Re: Catch Up", cc=ME, is_reply=True)]
    assert ME not in co.group_threads(rows, ME)[0]["people"]


def test_reached_out_comes_from_the_first_message_of_the_day():
    # He starts a thread and then follows up on it: the day's story is that he
    # reached out, not that he replied to himself.
    rows = [_row("a@b.com", "New idea", thread="T", is_reply=False),
            _row("a@b.com", "Re: New idea", thread="T", is_reply=True)]
    assert co.group_threads(rows, ME)[0]["reached_out"] is True


def test_a_reply_thread_is_not_reaching_out():
    rows = [_row("a@b.com", "Re: Old thread", thread="T", is_reply=True)]
    assert co.group_threads(rows, ME)[0]["reached_out"] is False


# --------------------------------------------------------------------------- #
# render_page
# --------------------------------------------------------------------------- #

from datetime import date

DAY = date(2026, 8, 21)


def test_the_page_separates_reaching_out_from_replying():
    threads = co.group_threads(
        [_row("kat@vendor.example", "checking in", thread="A"),
         _row("gavin.lawton@client.example", "Re: Catch Up", is_reply=True, thread="B")], ME)
    page = co.render_page(threads, DAY)
    reached, replied = page.split("### Replied")
    assert "checking in" in reached
    assert "Catch Up" in replied


def test_the_reply_prefix_is_dropped_from_the_heading():
    # Which section it is in already says it was a reply.
    threads = co.group_threads([_row("a@b.com", "RE: Re: Catch Up", is_reply=True)], ME)
    assert "**Catch Up**" in co.render_page(threads, DAY)


def test_a_multi_message_thread_says_how_many():
    threads = co.group_threads(
        [_row("a@b.com", "Re: X", is_reply=True, thread="T"),
         _row("a@b.com", "Re: X", is_reply=True, thread="T")], ME)
    assert "(2 messages)" in co.render_page(threads, DAY)


def test_a_single_message_thread_says_nothing_about_count():
    threads = co.group_threads([_row("a@b.com", "Re: X", is_reply=True)], ME)
    assert "messages)" not in co.render_page(threads, DAY)


def test_an_empty_section_gets_the_none_marker():
    threads = co.group_threads([_row("a@b.com", "Re: X", is_reply=True)], ME)
    page = co.render_page(threads, DAY)
    assert page.split("### Replied")[0].count("**None:**") == 1


def test_the_date_is_written_by_python():
    assert co.render_page([], DAY).startswith("## Correspondence: August 21, 2026")


def test_the_page_never_carries_a_body_or_a_snippet():
    # The promise of the whole feature: metadata only. A row that somehow arrived
    # carrying a body must not render one.
    row = _row("a@b.com", "Re: X", is_reply=True)
    row["body"] = "SECRET BODY TEXT"
    row["snippet"] = "SECRET SNIPPET"
    page = co.render_page(co.group_threads([row], ME), DAY)
    assert "SECRET" not in page


# --------------------------------------------------------------------------- #
# Where it writes
# --------------------------------------------------------------------------- #

def test_the_directory_is_not_the_vault_ingest_queue(monkeypatch):
    # The whole point of option B: anything under LEARNINGS_DIR becomes an
    # asserted wiki page, and these pages name people.
    monkeypatch.delenv("CORRESPONDENCE_DIR", raising=False)
    monkeypatch.setenv("LEARNINGS_DIR", "/tmp/vault/raw")
    assert co._correspondence_dir() != __import__("pathlib").Path("/tmp/vault/raw")
    assert co._correspondence_dir().name == "correspondence"


def test_the_directory_is_configurable(monkeypatch):
    monkeypatch.setenv("CORRESPONDENCE_DIR", "/tmp/elsewhere")
    assert str(co._correspondence_dir()) == "/tmp/elsewhere"


def test_correspondence_dir_resolves_a_relative_setting_under_the_config_dir(monkeypatch, tmp_path):
    """CORRESPONDENCE_DIR is a type="path" row: a relative value resolves
    against the config dir, not the process's working directory."""
    monkeypatch.setenv("SCRIBEJAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CORRESPONDENCE_DIR", "letters-under-config")

    assert co._correspondence_dir() == tmp_path / "letters-under-config"


# --------------------------------------------------------------------------- #
# The thread store. Every fact a page states about the gap between days —
# "open 4 days", "first contact", "she wrote and he never answered" — is read
# out of this file, so a wrong row is a wrong sentence about a real person.
#
# store_path() is redirected to tmp_path suite-wide by tests/conftest.py.
# --------------------------------------------------------------------------- #

TODAY = date(2026, 9, 2)


def _entry(thread="T1", subject="Proposal", people=None,
           inbound="", outbound=""):
    return {"thread_id": thread, "subject": subject,
            "people": people if people is not None else {"kat@x.example": "Kat"},
            "last_inbound": inbound, "last_outbound": outbound}


def test_an_unwritten_store_reads_as_empty():
    # First run on a fresh machine. Not an error, and not a crash.
    assert co.load_threads() == {}


def test_a_days_threads_come_back_on_the_next_read():
    co.remember_threads([_entry(outbound="2026-09-01 09:00")], TODAY)
    known = co.load_threads()
    assert list(known) == ["T1"]
    assert known["T1"]["last_outbound"] == "2026-09-01 09:00"
    assert known["T1"]["people"] == {"kat@x.example": "Kat"}


def test_first_seen_is_the_earliest_stamp_on_the_thread():
    co.remember_threads([_entry(inbound="2026-08-20 17:00",
                                outbound="2026-08-20 09:00")], TODAY)
    assert co.load_threads()["T1"]["first_seen"] == "2026-08-20 09:00"


def test_first_seen_does_not_move_forward_on_a_later_day():
    """The whole point of the field. If a second day overwrote it, every
    thread would read as one day old and "open 4 days" would never be true."""
    co.remember_threads([_entry(outbound="2026-08-20 09:00")], TODAY)
    co.remember_threads([_entry(outbound="2026-09-01 09:00")], TODAY)
    assert co.load_threads()["T1"]["first_seen"] == "2026-08-20 09:00"


def test_an_out_of_order_run_does_not_rewind_a_thread():
    """A backfill walks oldest-first, but --date can land any day at any time.
    A store that went backwards would report a thread as unanswered after he
    had already answered it."""
    co.remember_threads([_entry(outbound="2026-09-01 09:00")], TODAY)
    co.remember_threads([_entry(outbound="2026-08-20 09:00")], TODAY)
    assert co.load_threads()["T1"]["last_outbound"] == "2026-09-01 09:00"


def test_a_later_day_adds_the_other_side_without_losing_the_first():
    co.remember_threads([_entry(outbound="2026-08-31 09:00")], TODAY)
    co.remember_threads([_entry(inbound="2026-09-01 11:00")], TODAY)
    row = co.load_threads()["T1"]
    assert row["last_outbound"] == "2026-08-31 09:00"
    assert row["last_inbound"] == "2026-09-01 11:00"


def test_a_real_name_replaces_a_bare_address_across_days():
    co.remember_threads([_entry(people={"kat@x.example": "kat@x.example"},
                                outbound="2026-08-31 09:00")], TODAY)
    co.remember_threads([_entry(people={"kat@x.example": "Kat Cleveland"},
                                inbound="2026-09-01 11:00")], TODAY)
    assert co.load_threads()["T1"]["people"] == {"kat@x.example": "Kat Cleveland"}


def test_a_later_day_with_no_subject_keeps_the_one_we_had():
    co.remember_threads([_entry(subject="Proposal", outbound="2026-08-31 09:00")], TODAY)
    co.remember_threads([_entry(subject="", inbound="2026-09-01 11:00")], TODAY)
    assert co.load_threads()["T1"]["subject"] == "Proposal"


def test_an_entry_with_no_thread_id_is_skipped_not_stored_under_blank():
    co.remember_threads([_entry(thread="", outbound="2026-09-01 09:00")], TODAY)
    assert co.load_threads() == {}


# ---- owes_reply: whose turn is it -------------------------------------------

def test_owes_reply_is_true_when_they_wrote_last():
    assert co.owes_reply({"last_inbound": "2026-09-01 11:00",
                          "last_outbound": "2026-08-31 09:00"}) is True


def test_owes_reply_is_false_when_he_wrote_last():
    assert co.owes_reply({"last_inbound": "2026-08-31 09:00",
                          "last_outbound": "2026-09-01 11:00"}) is False


def test_owes_reply_compares_the_clock_not_just_the_day():
    """A message that arrived at 5pm after he answered at 9am is still his
    turn. A date-only comparison would call that day even and lose the thread
    from the page — which is the exact case this section exists to catch."""
    assert co.owes_reply({"last_inbound": "2026-09-01 17:00",
                          "last_outbound": "2026-09-01 09:00"}) is True


def test_owes_reply_is_false_when_nothing_ever_arrived():
    # A thread he started and nobody answered is not his turn.
    assert co.owes_reply({"last_inbound": "", "last_outbound": "2026-09-01 09:00"}) is False
    assert co.owes_reply({}) is False


# ---- days_since --------------------------------------------------------------

def test_days_since_counts_whole_days_from_the_stamps_date():
    assert co.days_since("2026-08-30 23:59", TODAY) == 3
    assert co.days_since("2026-09-02 00:01", TODAY) == 0


def test_days_since_reads_a_damaged_stamp_as_zero():
    # One damaged row costs its own note, not the page.
    assert co.days_since("", TODAY) == 0
    assert co.days_since("not a date", TODAY) == 0


# ---- pruning -----------------------------------------------------------------

def test_a_conversation_past_the_retention_window_is_dropped():
    old = (TODAY - timedelta(days=co.RETENTION_DAYS + 1)).isoformat()
    co.remember_threads([_entry(outbound=f"{old} 09:00")], TODAY)
    assert co.load_threads() == {}


def test_a_conversation_exactly_at_the_window_is_kept():
    edge = (TODAY - timedelta(days=co.RETENTION_DAYS)).isoformat()
    co.remember_threads([_entry(outbound=f"{edge} 09:00")], TODAY)
    assert list(co.load_threads()) == ["T1"]


def test_pruning_happens_on_write_so_the_store_cannot_grow_unbounded():
    """AGENTS.md: a store the daily job appends to prunes on write. Asserted
    on the file, not on a later read, because a filter applied at read time
    would leave the file growing forever."""
    old = (TODAY - timedelta(days=200)).isoformat()
    co.remember_threads([_entry(thread="OLD", outbound=f"{old} 09:00")],
                        TODAY - timedelta(days=200))
    co.remember_threads([_entry(thread="NEW", outbound="2026-09-01 09:00")], TODAY)
    written = json.loads(co.store_path().read_text())
    assert list(written["threads"]) == ["NEW"]


def test_a_row_carrying_no_stamp_at_all_is_dropped():
    """It can only be damage, and days_since reads a blank as 0 — so without
    an explicit rule it would be kept forever."""
    co.store_path().write_text(json.dumps({"threads": {"T1": {"subject": "x"}}}))
    co.remember_threads([], TODAY)
    assert co.load_threads() == {}


def test_running_the_same_day_twice_changes_nothing():
    """The plan's own check for this store, kept as a test. A re-run after a
    failure, and a --backfill that overlaps days already recorded, are both
    ordinary — neither may double a count or move a date."""
    entries = [_entry(inbound="2026-09-01 11:00", outbound="2026-09-01 09:00")]
    co.remember_threads(entries, TODAY)
    once = co.store_path().read_text()
    co.remember_threads(entries, TODAY)
    assert co.store_path().read_text() == once
