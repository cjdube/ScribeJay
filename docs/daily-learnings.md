# Daily learnings — Chrome and YouTube

Two tasks, one shape: read yesterday's activity from one channel, have the local
model draft a short review of what it taught, and write one Markdown file per
day into the journal folder (`LEARNINGS_DIR`, default `~/Documents/ScribeJay`).
They share the exclusion-filter and compaction helpers in
`scribejay/activity.py` and the write-or-email step in
`scribejay/sinks/vault.py`.

A third task, [ai_chat_learnings](ai-chat-learnings.md), covers the same ground
for AI-agent conversations and has its own doc because its sources are unusual.

| Task | Schedule | Channel |
|---|---|---|
| `scribejay/daily_chrome_learnings.py` | Daily 5:15 AM | Chrome browsing history |
| `scribejay/daily_youtube_learnings.py` | Daily 5:05 AM | YouTube Liked videos |

## Chrome

Reads the prior day's history via `scribejay/sources/chrome.py` and asks the
model for a compact daily log in two sections:

- **Tools & Tech Encountered**
- **Product & Strategy** — for product-management reading

Written as `Daily-Chrome-<date>.md`.

**Each site carries its top page *paths*, not just the domain.** This is the
detail that makes the review say what was being looked into rather than restate
a tab title. A domain alone ("github.com") tells the model nothing; the paths do.

**Exclusions.** Sites and pages matching `learnings.excluded_domains` or
`learnings.excluded_keywords` are skipped — see [configuration](configuration.md#structured-settings--persona-calendar-learnings).

**Known gap:** `NOISE_DOMAINS` drops `google.com`, so search-mediated research
never reaches the log. If a day's review looks thin, check the unfiltered
history before blaming the prompt.

### Reading the pages, not just the paths — off by default

A path is a guess. `/gemini-api/docs/models` tells the model a model list was
open; it does not tell it which models. Turning on
`SCRIBEJAY_WEB_FETCH_ENABLED` lets the task fetch up to
`SCRIBEJAY_WEB_FETCH_MAX_PAGES` of yesterday's pages, summarize each one
locally, and hand those summaries to the draft as `page_notes`.

The summaries then do a second job: a **Pages Read** section is appended to the
entry, one line per page, with the summary kept whole and the title linked. The
bullets above it are still the model's compression — that is what makes them
specific — and the section below is where the detail survives.

The fetch is done by this Mac. Firecrawl is an opt-in fallback for pages the
Mac cannot render, and only if `FIRECRAWL_API_KEY` is set — with it on, the
chosen URLs and their content leave the machine. The raw page text never
reaches the draft prompt; only the local summaries do. See
[docs/web-fetch.md](web-fetch.md) for the whole design, the exclusion rules and
the privacy note.

Off, nothing changes: no request, no key lookup, no extra model call.

## YouTube

Reads the prior day's Liked videos via `scribejay/sources/youtube.py` — title,
channel and description, **not** transcripts. The model writes a short synthesis
of what they teach; the list of the exact videos (verbatim titles, scheme-validated
URLs) is appended **in Python**, not by the model, so the links are always real.
Written as `Daily-YouTube-<date>.md`.

Likes are timestamped UTC and the day window is local — `_liked_local_date`
converts before comparing. See [timezones.md](timezones.md).

## Shared behavior

**A quiet day writes nothing.** No meaningful browsing, no Likes, or a draft
that amounts to "None" produces no file. This is normal, not a failure.

**A failed write emails the draft instead**, and pushes a phone alert — e.g.
when `LEARNINGS_DIR` points somewhere that doesn't exist. ScribeJay never
creates that folder, on purpose: a missing folder means the path is wrong, and
writing pages somewhere nobody reads is worse than failing. The draft cost a
model call; it's never silently dropped.

**The prompt is deliberately small and focused** so the on-device model produces
a complete draft rather than trailing off. See the small-local-model constraints
in [AGENTS.md](../AGENTS.md) and [docs/model-constraints.md](model-constraints.md).

## Where the output goes

`LEARNINGS_DIR` — a plain folder or an Obsidian vault, whichever you set in
`scribejay settings`. It is write-only: ScribeJay drops dated Markdown there and
never reads it back. Sent mail is deliberately kept out of it and goes to
`CORRESPONDENCE_DIR` instead, so a folder that feeds a note-taking pipeline does
not turn the people you email into note entities
([daily-correspondence.md](daily-correspondence.md)).

## Related

- [docs/ai-chat-learnings.md](ai-chat-learnings.md) — the third learnings task
- [docs/daily-commits.md](daily-commits.md) — the building half of the same daily record
- [docs/configuration.md](configuration.md#structured-settings--persona-calendar-learnings) — the exclusion keys
