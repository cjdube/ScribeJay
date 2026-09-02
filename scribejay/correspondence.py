"""Yesterday's mail, turned into a record of who he talked to.

The record says what he built (scribejay/daily_commits.py), what he read
(daily_chrome_learnings) and where his hours went (AI Session Time Blocks). None of it
says who he talked to. This is the only source that does.

**Both directions.** Sent mail alone answers "who did I write to", which he
already knows. The question worth a page is who wrote to *him* and is still
waiting — so `render_page` carries four sections: what he started, what he
answered, what arrived unanswered, and what has been open long enough to have
been forgotten. The last two come from the inbound half and the thread store.

**Metadata only.** Both fetchers in `scribejay.sources.gmail` ask Gmail for
headers and nothing else, so the bodies are never fetched rather than fetched
and discarded. What is recorded is who, when, about what subject line, and
whose turn it is.

**Every word a stranger chose goes through `safe_label` first.** A sender picks
their own subject line and display name, and both land in a Markdown file he
trusts — a subject of `[Unpaid invoice](http://evil.example)` would otherwise
render as a live link inside the vault. See scribejay/core/text.py. The store
is the same surface a day later: text written into it today is rendered
tomorrow, so nothing is trusted for having been saved once.

**No model call**, and not for want of one: with no bodies, the model would have
only a subject and some names, and any sentence richer than those is invented. So
the page is assembled in Python, the way scribejay/strava_download.py maps Strava
fields onto calendar events without a natural-language step. That also means the
untrusted text above never reaches a prompt at all — there is no prompt.
"""

import re
from datetime import date
from email.utils import getaddresses
from pathlib import Path

from scribejay.core import config
from scribejay.core.text import safe_label
from scribejay.core.store import atomic_write_json, load_json, locked

DEFAULT_CORRESPONDENCE_DIR = str(Path.home() / "Documents" / "ScribeJay" / "correspondence")

# Subjects that are machinery rather than correspondence. Everything ScribeJay
# sends itself is already caught by the self-addressed rule below; this list is
# for mail to a real outside address that still is not a conversation. Expected
# to grow — a missed one costs a junk line in a page, not a failure.
NOISE_SUBJECTS = ("unsubscribe",)


def _correspondence_dir() -> Path:
    """Where the pages go. **Not** LEARNINGS_DIR.

    LEARNINGS_DIR is the vault's `raw/`, ObsidianWikiAgent's ingest queue, and
    anything dropped there becomes an asserted wiki page — which would turn the
    people he emails and the companies they work for into wiki entities. A record
    of who he wrote to is a diary, so it gets its own directory outside the queue."""
    return config.resolve_path(config.getenv("CORRESPONDENCE_DIR", DEFAULT_CORRESPONDENCE_DIR))


def _recipients(row: dict) -> list:
    """Every address on To and Cc, lowercased. Parsed with the stdlib's own header
    parser rather than split on commas: a display name may contain one."""
    return [addr.lower() for _, addr in getaddresses([row.get("to", ""), row.get("cc", "")])
            if addr]


# An address inside angle brackets, for headers the stdlib parser gives up on.
_ANGLE_ADDR = re.compile(r"<([^<>@\s]+@[^<>@\s]+)>")


def _salvage(headers: list, me: str) -> dict:
    """Addresses out of a header `getaddresses` could not read, as
    {address: address}.

    An **unquoted** display name containing brackets or parentheses makes
    `email.utils.getaddresses` return an empty address rather than a bad name:
    `[Your bank](http://evil) <sneak@x.example>` parses to ("", ""). So a
    sender who names themselves that way vanishes from the page altogether,
    and the record says a message arrived from nobody.

    Real clients quote such a display name; this is the backstop for one that
    does not. It keeps only the address, which is the half of a header a
    sender cannot forge."""
    found = {}
    for header in headers:
        for addr in _ANGLE_ADDR.findall(header or ""):
            if addr.lower() != me:
                found[addr.lower()] = addr.lower()
    return found


def _people(row: dict, me: str) -> dict:
    """The people on one message as {address: label}, never including the user.

    Keyed on the address because the same person arrives named on one message and
    bare on the next — a set of strings would list them twice."""
    out = {}
    for name, addr in getaddresses([row.get("to", ""), row.get("cc", "")]):
        if not addr or addr.lower() == me:
            continue
        label = (name or "").strip().strip('"')
        out[addr.lower()] = label or addr
    return out or _salvage([row.get("to", ""), row.get("cc", "")], me)


def _merge_people(into: dict, more: dict) -> None:
    """Fold one message's people into a thread's, preferring a real name over a
    bare address whichever order the two arrive in."""
    for addr, label in more.items():
        if addr not in into or into[addr] == addr:
            into[addr] = label


def filter_noise(rows: list, me: str, logger=None) -> list:
    """Drop what is machinery rather than correspondence.

    Two rules, and the first is the one that matters: **mail addressed only to
    himself is not correspondence.** That is every self-sent digest or rollup
    ScribeJay's sibling tasks send — which is why a filter is the first thing
    this needs rather than a refinement to add later."""
    me = (me or "").lower()
    kept = []
    for row in rows:
        addresses = _recipients(row)
        if not addresses or all(a == me for a in addresses):
            continue
        if (row.get("subject") or "").strip().lower() in NOISE_SUBJECTS:
            continue
        kept.append(row)
    if logger and len(kept) < len(rows):
        logger.info(f"filtered {len(rows) - len(kept)} of {len(rows)} sent message(s) "
                    "as self-addressed or machine mail")
    return kept


def filter_inbound_noise(rows: list, me: str, logger=None) -> list:
    """Drop what arrived but is not correspondence.

    Much thinner than `filter_noise`, and deliberately so: Gmail's own category
    tabs already did the heavy lifting server-side, in the query
    (`sources/gmail.py`). Measured over seven real days that cut 28 inbound
    messages to 3, and the 3 were the only humans in the set. The
    self-addressed rule has no inbound equivalent either — anything he sent
    carries Gmail's SENT label and the query already excluded it.

    So what is left is the same subject list the sent side uses, applied for
    symmetry: an unsubscribe confirmation really does arrive."""
    kept = [row for row in rows
            if (row.get("subject") or "").strip().lower() not in NOISE_SUBJECTS]
    if logger and len(kept) < len(rows):
        logger.info(f"filtered {len(rows) - len(kept)} of {len(rows)} arrived "
                    "message(s) as machine mail")
    return kept


def _inbound_people(row: dict, me: str) -> dict:
    """Who is on an arrived message: the sender, plus anyone else on To and Cc
    who is not him.

    The sender is merged first so their label wins — a bare address on the To
    line must not overwrite the name the From header carried."""
    out = {}
    for name, addr in getaddresses([row.get("from", "")]):
        if not addr or addr.lower() == me:
            continue
        out[addr.lower()] = (name or "").strip().strip('"') or addr
    # The salvage matters most here: From is the one header a stranger writes.
    out = out or _salvage([row.get("from", "")], me)
    _merge_people(out, _people(row, me))
    return out


def group_day(sent_rows: list, inbox_rows: list, me: str) -> list:
    """One entry per conversation touched today, both directions folded in.

    Grouped on Gmail's own thread id, so four messages over an evening read as
    one exchange rather than four.

    `reached_out` is taken from the FIRST **sent** message of the day on the
    thread: answering is routine, and deciding to start something is the part
    of a day worth recording. Sent rows are walked first and the "first" is
    tracked explicitly rather than inferred from whether the thread already
    exists — otherwise a thread he opened at 9am and she answered at 11am
    would read as *her* thread, because her message would have created it.
    """
    threads: dict = {}

    def touch(row: dict) -> dict:
        key = row.get("thread_id") or row.get("message_id")
        thread = threads.get(key)
        if thread is None:
            thread = threads[key] = {
                "thread_id": key,
                "subject": row.get("subject") or "(no subject)",
                "people": {},
                "messages": 0,
                "reached_out": False,
                "last_inbound": "",
                "last_outbound": "",
            }
        thread["messages"] += 1
        return thread

    started = set()
    for row in sent_rows:
        thread = touch(row)
        _merge_people(thread["people"], _people(row, me))
        thread["last_outbound"] = max(thread["last_outbound"], row.get("date", ""))
        if thread["thread_id"] not in started:
            started.add(thread["thread_id"])
            thread["reached_out"] = not row.get("is_reply")

    for row in inbox_rows:
        thread = touch(row)
        _merge_people(thread["people"], _inbound_people(row, me))
        thread["last_inbound"] = max(thread["last_inbound"], row.get("date", ""))

    return list(threads.values())


def _subject_label(subject: str) -> str:
    """The subject without Gmail's reply prefix — the thread is already marked as
    a reply by which section it lands in, so "Re:" would say it twice."""
    label = subject.strip()
    while label[:3].lower() == "re:":
        label = label[3:].strip()
    return label or "(no subject)"


# How long a thread sits with the ball in his court before the page nags about
# it. Three days lets a Friday message wait out a weekend without becoming an
# item on Saturday's page.
QUIET_AFTER_DAYS = 3


def _person(addr: str, name: str, known_addresses: set) -> str:
    """One person, as the page names them.

    A display name is chosen by whoever sent the mail, so it goes through
    `safe_label` and — for anybody not already in the store — is shown next to
    the real address. A stranger can set their display name to his wife's; they
    cannot set the address. Anyone he has corresponded with before is named
    plainly, because by then the name is one he has already checked."""
    shown = safe_label(name) or safe_label(addr) or "(unnamed)"
    if addr in known_addresses or shown == safe_label(addr):
        return shown
    return f"{shown} <{safe_label(addr)}>"


def _people_label(people: dict, known_addresses: set) -> str:
    return ", ".join(_person(addr, name, known_addresses)
                     for addr, name in people.items()) or "(nobody named)"


def _line(subject: str, people: str, suffix: str = "") -> str:
    """One bullet. The subject is neutralized and then falls back to a
    placeholder, because a subject made only of Markdown syntax is emptied by
    `safe_label` and would otherwise render as an empty bold run."""
    label = safe_label(_subject_label(subject)) or "(no subject)"
    return f"- **{label}** — {people}{suffix}"


def _turn(thread: dict, known: dict) -> bool:
    """Whose turn it is once today's activity is folded onto what we knew.

    Read from the merge rather than from today alone: a message that arrived
    yesterday and was answered this morning is settled, and one that arrived
    last week and is still unanswered is not — neither fact is visible in a
    single day's rows."""
    prior = known.get(thread["thread_id"], {})
    return owes_reply({
        "last_inbound": max(thread["last_inbound"], prior.get("last_inbound") or ""),
        "last_outbound": max(thread["last_outbound"], prior.get("last_outbound") or ""),
    })


def quiet_threads(known: dict, active: set, day) -> list:
    """Older conversations where the ball is still in his court, as
    (age_in_days, thread_id, row), oldest first.

    Anything touched today is excluded — the "came in" section already carries
    it, and a thread cannot be both today's news and forgotten. Sorted oldest
    first so the one most likely to have been dropped reads first.

    Public because the task asks it whether a day with no mail is still worth a
    page: a quiet Sunday with a week-old unanswered message has something to
    say, and skipping it would hide exactly the thread this section is for."""
    aged = []
    for key, row in known.items():
        if key in active or not owes_reply(row):
            continue
        age = days_since(row.get("last_inbound", ""), day)
        if age >= QUIET_AFTER_DAYS:
            aged.append((age, key, row))
    return sorted(aged, key=lambda triple: -triple[0])


def render_page(threads: list, known: dict, day) -> str:
    """The whole page, in Python. See the module docstring for why no model runs
    over this: with no bodies, anything beyond the subject and the names would be
    invented.

    `known` is the thread store as it stood **before** today was folded in — it
    is what makes "first contact" and an age in days true statements rather
    than guesses. Fold today in afterwards, with `remember_threads`.
    """
    known_addresses = {addr for row in known.values()
                       for addr in (row.get("people") or {})}
    active = {t["thread_id"] for t in threads}

    def today_line(thread: dict) -> str:
        count = f" ({thread['messages']} messages)" if thread["messages"] > 1 else ""
        first = " *(first contact)*" if thread["thread_id"] not in known else ""
        return _line(thread["subject"],
                     _people_label(thread["people"], known_addresses),
                     count + first)

    reached = [today_line(t) for t in threads if t["reached_out"]]
    replied = [today_line(t) for t in threads
               if t["last_outbound"] and not t["reached_out"]]
    incoming = [today_line(t) for t in threads
                if t["last_inbound"] and _turn(t, known)]

    quiet = [_line(row.get("subject", ""),
                   _people_label(row.get("people") or {}, known_addresses),
                   f" — {age} days")
             for age, _, row in quiet_threads(known, active, day)]

    def block(title: str, lines: list) -> list:
        if not lines:
            return [f"### {title}", "- **None:** [No qualifying items for this section]"]
        return [f"### {title}"] + lines

    return "\n".join(
        [f"## Correspondence: {day:%B %-d, %Y}", ""]
        + block("Reached out", reached) + [""]
        + block("Replied", replied) + [""]
        + block("Came in, no answer yet", incoming) + [""]
        + block("Gone quiet", quiet) + [""]
    )


# --------------------------------------------------------------------------- #
# The thread store — what makes a day's page say more than the day.
#
# One page built from one day can only list that day. "Open four days", "first
# time he has written to her", "she answered and he never did" are all facts
# about the gap between days, so they need something that survives the run.
# Deterministic Python, not a model: these are dates (AGENTS.md).
# --------------------------------------------------------------------------- #

STORE_NAME = "correspondence_threads.json"

# How long a finished conversation stays remembered. Long enough that "first
# time in months" is a true sentence rather than a guess, short enough that the
# file stays small on a mailbox that has run for years.
RETENTION_DAYS = 90


def store_path():
    """Under `~/.scribejay` via resolve_path, never beside the source tree —
    installed as a tool that would be site-packages, which a reinstall wipes.
    A function rather than a constant so a test's redirect is seen."""
    return config.resolve_path(STORE_NAME)


def load_threads() -> dict:
    """Every conversation remembered so far, keyed on Gmail's thread id.

    Read without the lock on purpose. This is the only writer, the file is
    replaced atomically so a reader sees the old or the new whole file, and a
    momentarily stale answer costs one continuity note rather than a page.
    """
    return load_json(store_path(), {}).get("threads", {})


def _stamp_date(stamp: str) -> str:
    """The date half of a "YYYY-MM-DD HH:MM" stamp."""
    return (stamp or "")[:10]


def days_since(stamp: str, today: date) -> int:
    """Whole days between a stored stamp and a date.

    Returns 0 for a missing or unreadable stamp, so one damaged row costs its
    own note rather than the page. Callers that must distinguish "today" from
    "unknown" check the stamp themselves — `_prune` does."""
    try:
        seen = date.fromisoformat(_stamp_date(stamp))
    except ValueError:
        return 0
    return (today - seen).days


def _last_activity(thread: dict) -> str:
    """The later of the two stamps, or "" for a row that carries neither."""
    return max(thread.get("last_inbound") or "", thread.get("last_outbound") or "")


def owes_reply(thread: dict) -> bool:
    """True when the last thing to happen on the thread was somebody writing
    to HIM. A thread he answered last is their turn, not his.

    Compared as whole stamps rather than dates: a message that arrived at 5pm
    after he answered at 9am is still his turn, and a date-only comparison
    would call that day even."""
    inbound = thread.get("last_inbound") or ""
    return bool(inbound) and inbound > (thread.get("last_outbound") or "")


def _prune(known: dict, today: date) -> dict:
    """Drop conversations with no activity inside RETENTION_DAYS.

    Pruned on write so a store the daily job appends to cannot grow unbounded
    (AGENTS.md). A row carrying no stamp at all is dropped too: it can only be
    damage, and `days_since` would read it as 0 and keep it forever."""
    kept = {}
    for key, row in known.items():
        stamp = _last_activity(row)
        if stamp and days_since(stamp, today) <= RETENTION_DAYS:
            kept[key] = row
    return kept


def remember_threads(day_threads: list, today: date) -> None:
    """Fold one day's conversations into the store, prune, and write.

    Each entry is {thread_id, subject, people, last_inbound, last_outbound},
    where either stamp may be "" — a day on which only one side wrote.

    Held under the lock across the whole read-modify-write. A backfill runs
    this once per day in a loop, and a launchd run can overlap a hand-run one;
    without the lock the second would write a store built from a state the
    first had already moved past."""
    with locked(store_path()):
        data = load_json(store_path(), {})
        known = data.get("threads", {})
        for entry in day_threads:
            key = entry.get("thread_id")
            if not key:
                continue
            row = known.setdefault(key, {"first_seen": "", "people": {}})
            row["subject"] = entry.get("subject") or row.get("subject", "")
            row.setdefault("people", {})
            _merge_people(row["people"], entry.get("people") or {})
            stamps = [s for s in (entry.get("last_inbound"),
                                  entry.get("last_outbound")) if s]
            # Each side moves forward only. A backfill walks oldest day first,
            # but a hand-run --date can land out of order, and a store that
            # went backwards would report a thread as unanswered after he
            # had already answered it.
            for side in ("last_inbound", "last_outbound"):
                stamp = entry.get(side) or ""
                if stamp > (row.get(side) or ""):
                    row[side] = stamp
            if stamps:
                earliest = min(stamps)
                if not row["first_seen"] or earliest < row["first_seen"]:
                    row["first_seen"] = earliest
        data["threads"] = _prune(known, today)
        atomic_write_json(store_path(), data)
