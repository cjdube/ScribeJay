# Daily correspondence — who he talked to, and who is still waiting

`scribejay/daily_correspondence.py`, daily at 5:20 AM. Reads yesterday's Gmail
**metadata** in both directions — headers, never bodies — groups it by
conversation and writes `Correspondence-<date>.md` into `CORRESPONDENCE_DIR`.

**Why it exists.** The record already covers what was built
([daily-commits.md](daily-commits.md)), what was read
([daily-learnings.md](daily-learnings.md)) and where the hours went
([AI Session Time Blocks](ai-session-time-blocks.md)). None of it says who the
day was spent talking to. Mail is the only source that does, and
`gmail.readonly` is already consented, so it costs no new OAuth.

**Why it reads the inbox too.** Sent mail alone answers "who did I write to",
which he already knows — he wrote them. The question worth a page is who wrote
to *him* and never got an answer. That is the half a sent-only record cannot
see, and the reason the page has four sections instead of two.

## What it reads

Two fetchers in `scribejay/sources/gmail.py`, sharing one client and one
list-then-get loop:

| Fetcher | Query | Row |
|---|---|---|
| `fetch_sent_metadata` | `in:sent` | `{message_id, thread_id, to, cc, subject, date, is_reply}` |
| `fetch_inbox_metadata` | `INBOX_SCOPE` + `INBOX_CATEGORY_FILTER` | the above plus `from`, `header_id`, `references`, minus `is_reply` |

Gmail is asked with `format="metadata"` and an explicit `metadataHeaders` list,
so a body is never *fetched* — not fetched and then discarded. The test asserts
that on the request arguments, because a row with no body would look identical
either way.

**"Mail that arrived", not "mail still in the inbox".** `INBOX_SCOPE` is
`-in:sent -in:draft -in:spam -in:trash -in:chats` rather than `in:inbox`. The
job runs at 5:20 the next morning, by which time anything already dealt with
has often been archived — and an archived message he never answered is exactly
the one worth recording.

**The day window is sent as epoch seconds.** Gmail's `after:`/`before:` take
whole days in the *account's* timezone, which is not necessarily the machine's,
and would quietly slice the day wrong ([timezones.md](timezones.md)).

## No model call

Deliberate, and a deviation worth naming. With no bodies the model would have a
subject line and some names, and any sentence richer than those is invented. The
subject line is already the most accurate description of the exchange that
exists, so the page is assembled in Python — the way
`scribejay/strava_download.py` maps Strava fields onto calendar events with no
natural-language step.

It also settles the security question that reading the inbox opens. There is no
prompt for a stranger's subject line to steer, because there is no prompt.

## Untrusted text

A sender picks their own subject line and their own display name, and both land
in a Markdown file inside a folder he trusts. A subject of
`[Unpaid invoice](http://evil.example)` would render as a live link with the
real destination hidden behind friendly words.

Every subject and every name goes through `safe_label`
(`scribejay/core/text.py`) first. It turns Markdown and HTML syntax into spaces
rather than deleting it, so `a|b` reads as `a b`, and truncates at 120
characters. The real URL stays visible as plain text — that is the point:
breaking the syntax leaves the deception on the page where it can be seen.

Not an escape function. `\[Unpaid invoice\]` would be faithful to the original
bytes, but a record of who wrote to you is a diary, not a transcript.

**The store is the same surface a day later.** Text saved today is rendered
tomorrow, so nothing is trusted for having been written to disk once.

**People he does not know are shown with their real address.** A stranger can
set their display name to anything, including a name he trusts; they cannot set
the address. `Craig Dube <impostor@x.example>` is the case this exists for.
Anyone already in the thread store is named plainly, because by then the name is
one he has checked.

**A sender cannot erase themselves.** `email.utils.getaddresses` returns
`("", "")` for an unquoted display name containing brackets or parens, which
would render the most hostile message in the mailbox as arriving from nobody.
`_salvage` pulls the address back out of the angle brackets when the parser
gives up.

## The noise filters

Sent and inbound need different filters, because the noise is different.

**Sent.** In a sample fortnight, 4 of 10 sent messages were software writing to
the user rather than the user writing to anyone.

| Rule | Catches |
|---|---|
| Every recipient is the user himself | ScribeJay's own vault-write fallback and failure notices, and any other tool that mails him a report |
| Subject is in `NOISE_SUBJECTS` | An `unsubscribe` click, which really is sent mail |

The self-addressed rule is the principled one and comes first. `NOISE_SUBJECTS`
is a short list expected to grow; a missed entry costs one junk line in a page,
not a failed run.

A rejected third rule: *"the recipient is not a real address."* Real data
disproved it — the unsubscribe went to
`32.MRTVIML…@unsubscribe2.customer.io`, which is a perfectly valid address.

**Inbound.** Two rules, and the first is Gmail's.

| Rule | Catches |
|---|---|
| Gmail's category tabs, in the query | newsletters, notifications, social, forums |
| Subject starts with a `CALENDAR_SUBJECT_PREFIXES` entry | `Canceled:`, `Invitation:`, `Accepted:` and friends |

Google Calendar writes its own subject lines and Gmail files them in the
**primary** tab, so the category filter never sees them. A cancellation is a
real thing that happened to the day, but nobody is waiting on an answer — left
in, it sits in "Gone quiet" forever. Matched as a prefix on the *raw* subject,
so `Re: Invitation: …` — a human writing back — is kept. The sent side does not
apply this list: he is allowed to write "Canceled: our call" to a person.

The same prefixes are checked again in `quiet_threads`, against the stored
subject. Adding a filter does not retroactively remove what it would have
caught, and "Gone quiet" is the only section a thread can sit in for 90 days.

The category filter itself:
`-category:promotions -category:social -category:updates -category:forums`. A
hand-kept list of newsletter senders would need a new entry every time he
subscribes to something and would never be finished — the same reject-list trap
the web fetcher avoids (AGENTS.md). Gmail already does this classification, per
account, for free.

Measured over seven real days it cut 28 arrived messages to 3, and all 3 were
people. The closest call it dropped was a mailing-list note about one of his own
talks, which lands in `updates`; that is the known cost of the filter.

## The page

Four sections. The first two come from the **first** message of the day on each
thread; the last two are the reason the inbox is read at all.

- `### Reached out` — a thread he started. Deciding to start something is the
  part of a day worth recording.
- `### Replied` — answering, which is routine.
- `### Came in, no answer yet` — arrived, and it is still his turn.
- `### Gone quiet` — open past `QUIET_AFTER_DAYS` (3), from the thread store,
  oldest first, with an age in days.

One line per conversation, not per message: `**Subject** — Name, Name (3
messages)`. Threads are grouped on Gmail's own thread id, and the `Re:` prefix
is stripped because the section already says it was a reply. A thread never seen
before is marked `*(first contact)*`.

**Sent rows are walked first.** Her same-day reply must not decide whether he
reached out, or every conversation he opened would read as one he was dragged
into.

**Whose turn it is compares whole timestamps, not dates.** A message that
arrived at 17:00 after he answered at 09:00 is still his turn. Comparing dates
would call that thread settled.

**Three days before a thread counts as quiet.** That lets a Friday message wait
out a weekend without becoming an item on Saturday's page. A thread touched
today is never also quiet — it is today's news.

**People are keyed on address, not on display name.** The same person arrives
named on one message and bare on the next — Diego was on one real thread as both
`Diego M. Oppenheimer` and `diego@meetup.example`, and a set of strings listed
him twice. A real name beats a bare address whichever order the two arrive in.

**One person with two addresses is named once.** A real page read
`Derek Plautz, Derek Plautz`. The merge is on the *rendered* string, so two
known addresses under one name collapse while a stranger borrowing that name
renders with their address and survives as a separate entry.

**His own To: line counts as knowing someone.** On day one both of Derek's
addresses were new, so both printed an address and the two could not merge.
Addresses on a thread **he started** are ones he typed, and a stranger cannot
put themselves in his own To: line — so they are named plainly. A *reply* does
not count: its To: line is whatever the sender put in their From:, and proves
nothing.

## The thread store

`~/.scribejay/correspondence_threads.json`, one row per Gmail thread:
`{subject, people, first_seen, last_inbound, last_outbound}`, pruned at 90 days.

It is what makes "first contact" and "10 days" true statements rather than
guesses. Three rules are pinned by tests:

- `first_seen` only ever moves **backwards** — a backfill of an older day
  corrects it; a later day must not.
- `last_inbound` and `last_outbound` only ever move **forwards**.
- The page is rendered from the store **as it stood before this run**, and the
  day is folded in afterwards. Reversed, every thread reads as already known and
  every age as zero.

## Where it writes, and why not the vault

`CORRESPONDENCE_DIR`, default `~/Documents/ScribeJay/correspondence`.

**Not `LEARNINGS_DIR`.** The journal folder is often an Obsidian vault feeding a
note-taking pipeline, and anything dropped there can become an asserted note —
which would turn the people he emails and the companies they work for into note
entities. A record of who he wrote to is a diary, not a knowledge base. Keeping
it in a sibling folder costs one setting and removes the whole question.

## Behavior

**A day with no qualifying mail and no quiet threads writes nothing.** Normal on
a weekend. A quiet Sunday with a week-old unanswered message still gets a page,
because that thread is the whole point of the section.

**One failing fetcher still writes the other half, with a WARNING.** A dead
source reads as an empty day to its caller, but never silently — that line is
what separates a missing page caused by a broken API from one caused by a quiet
day.

**Both fetchers failing writes nothing.** A page built from two dead sources
reads as a day he spoke to nobody, which is a different day. `_messages` keeps a
failure (`None`) apart from an empty day (`[]`) precisely so this case can be
told apart.

**Not knowing the mailbox owner's address is a hard failure.** Every rule on the
page is "everyone who is not him"; with no address the whole day looks like mail
to strangers, which is worse than writing nothing. Gmail is not even queried
until the identity resolves.

## Running it by hand

```bash
.venv/bin/python -m scribejay.daily_correspondence
```

`--date 2026-08-21` writes one named day; `--backfill 14` writes each of the last
14 days, oldest first. A backfill is **one run** in the dashboard's history, not
N ([logs.md](logs.md)), and one day whose fetch fails does not stop the rest.

**A backfill is not a safe way to rebuild old pages.** Re-running a day
overwrites that day's file with what Gmail can see *today*, and `in:sent`
excludes trash. Measured on this mailbox: the page written on 31 Aug named Max
Ciccotosto, and a backfill run on 2 Sep found nothing at all for that day,
because the message had since been deleted. The page written on the day is the
accurate record; a backfill over it silently deletes real history. Backfill
into a **new or empty** `CORRESPONDENCE_DIR`, or onto days that have no page
yet.

The store is safe to repeat: stamps only move in one direction, `first_seen`
only moves backwards, so a re-run lands the same values or better ones.

## Related

- [docs/architecture.md](architecture.md) — the pipeline shape this task follows
- [docs/daily-commits.md](daily-commits.md) — the building half of the same daily record
- [docs/timezones.md](timezones.md) — why the window is sent as epoch seconds
