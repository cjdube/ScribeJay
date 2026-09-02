"""Yesterday's sent mail, turned into a record of who was written to.

The record says what he built (scribejay/daily_commits.py), what he read
(daily_chrome_learnings) and where his hours went (AI Session Time Blocks). None of it
says who he talked to. This is the only source that does.

**Metadata only.** `scribejay.sources.gmail.fetch_sent_metadata` asks Gmail for
headers and nothing else, so the bodies are never fetched rather than fetched and
discarded. What is recorded is who, when, about what subject line, and whether it
was an answer or a decision to reach out.

**No model call**, and not for want of one: with no bodies, the model would have
only a subject and some names, and any sentence richer than those is invented. So
the page is assembled in Python, the way scribejay/strava_download.py maps Strava
fields onto calendar events without a natural-language step. The subject line the
user wrote is the most accurate description of the exchange that exists.
"""

from datetime import date
from email.utils import getaddresses
from pathlib import Path

from scribejay.core import config
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
    return out


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


def group_threads(rows: list, me: str) -> list:
    """One row per conversation, in the order it was first written to.

    Grouped on Gmail's own thread id, so four messages over an evening read as one
    exchange rather than four. `reached_out` is taken from the FIRST message of
    the day on the thread: answering is routine, and deciding to start something
    is the part of a day worth recording."""
    threads: dict = {}
    for row in rows:
        key = row.get("thread_id") or row.get("message_id")
        thread = threads.get(key)
        if thread is None:
            threads[key] = {
                "thread_id": key,
                "subject": row.get("subject") or "(no subject)",
                "people": _people(row, me),
                "messages": 1,
                "reached_out": not row.get("is_reply"),
                "first": row.get("date", ""),
            }
            continue
        thread["messages"] += 1
        _merge_people(thread["people"], _people(row, me))
    return list(threads.values())


def _subject_label(subject: str) -> str:
    """The subject without Gmail's reply prefix — the thread is already marked as
    a reply by which section it lands in, so "Re:" would say it twice."""
    label = subject.strip()
    while label[:3].lower() == "re:":
        label = label[3:].strip()
    return label or "(no subject)"


def render_page(threads: list, day) -> str:
    """The whole page, in Python. See the module docstring for why no model runs
    over this: with no bodies, anything beyond the subject and the names would be
    invented."""
    reached = [t for t in threads if t["reached_out"]]
    replied = [t for t in threads if not t["reached_out"]]

    def block(title: str, rows: list) -> list:
        lines = [f"### {title}"]
        if not rows:
            lines.append("- **None:** [No qualifying items for this section]")
            return lines
        for t in rows:
            people = ", ".join(t["people"].values()) or "(no recipient)"
            count = f" ({t['messages']} messages)" if t["messages"] > 1 else ""
            lines.append(f"- **{_subject_label(t['subject'])}** — {people}{count}")
        return lines

    return "\n".join(
        [f"## Correspondence: {day:%B %-d, %Y}", ""]
        + block("Reached out", reached) + [""]
        + block("Replied", replied) + [""]
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
