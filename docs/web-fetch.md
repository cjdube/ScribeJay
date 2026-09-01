# Web fetch — reading the page instead of guessing from the path

**Off by default.** Nothing here runs until `SCRIBEJAY_WEB_FETCH_ENABLED=1`.

## The problem it solves

`daily_chrome_learnings` shows the model a domain, a title, and up to six page
*paths* per site. The model has to infer what a page said from its URL. A
bullet built that way is a guess dressed as a fact — "looked into Gemini API
models" when the page was actually a pricing change.

With web fetch on, a few of yesterday's pages are read for real, and the review
is written from what they said.

## The shape

    chrome history
      -> candidate_urls()      pick pages worth fetching   (activity.py, Python)
      -> fetch_pages()         get the text                (sources/web_fetch.py)
      -> summarize_pages()     3-5 neutral sentences each  (one local model call per page)
      -> page_notes block      capped at 4,000 characters
      -> the ordinary draft prompt
      -> pages_read_section()  the same notes, kept whole  (journal.py, Python)

Every step degrades to the step before it. A page that will not fetch, will not
summarize, or trips an exclusion is simply absent, and the day gets the
metadata-only draft it would have got with the feature off. Enrichment never
fails a morning.

## Which pages get fetched — `activity.py:candidate_urls`

Deterministic Python, not a model call. Asking a small local model at 5:15 AM
which pages look interesting buys nothing a sort cannot do, and adds a call that
can time out or return nothing.

**An allow list, not a reject list.** A page is fetched only if its path looks
like published writing — a headline slug of three or more real words, or a
one-word page sitting under a section that publishes (`/blog/`, `/docs/`,
`/news/` …) and carrying no identifier. Anything unclassifiable is skipped.
That costs a bullet now and then; a reject list instead fails open, and the
measured cost of that is in "the picker was the bug" below.

**Rejected before the allow list even runs:** anything the learnings exclusions
already reject (domain, title or path); non-`http(s)` URLs, via
`core/urls.py:safe_url`; private and local hosts (`localhost`, `127.*`, `10.*`,
`192.168.*`, `172.16–31.*`, `*.local`); session and account paths (`/login`,
`/reset-password`, `/checkout` …), matched on path *tokens* so `/descartes` is
not read as a cart; and file extensions that are not readable text (`.pdf`,
`.zip`, `.png`, `.mp4` …).

**Off-topic sections are dropped too.** `/sports/`, `/entertainment/`,
`/recipes/`, `/mlb/` and their neighbours, matched as whole path segments so
`/enterprise/` survives. This one *is* a reject list, and deliberately: the allow
list answers a privacy question, where failing open is unacceptable, and this
answers a relevance one, where failing open costs a fetch and a model call.
They are not the same test — "rigatoni-with-marinated-tomatoes-and-burrata" is
published writing by every measure `_looks_published` applies.

**The query string never leaves.** `_fetch_url` sends scheme, host and path
only. An article renders the same without `?utm_source=`, and the query string
is where the order ids, reservation codes and SSO `state` tokens live.

**Ranked by** path depth (an article beats an index) minus visit count (a page
opened once was read on purpose; a page opened nine times is a dashboard), then
**round-robin across domains** — five pages should be five things looked into,
not five tabs of the same doc site.

## Who does the fetching — `sources/web_fetch.py`

**Local first, and free.** `requests.get` with an explicit timeout, a plain user
agent, a response-size cap, and text extracted by
[trafilatura](https://trafilatura.readthedocs.io/) — an optional extra, imported
lazily. Not installed reads as "no text", never as an ImportError up through a
5:15 AM task.

**A site that says no is skipped.** HTTP 401, 403, 407, 429 and 451 return
`{"error": "blocked: HTTP …"}` and that is the end of that page. There is no
retry, no alternate user agent, and no stealth proxy — routing around a block is
exactly the scraping-SaaS workaround AGENTS.md rules out.

**A refusal also ends the backend chain.** `fetch_page` used to treat a
`blocked:` error like any other failure and move on to Firecrawl, which is the
same workaround wearing a different hat — observed live, where a local 403 from
arstechnica.com was followed by a successful Firecrawl fetch of the article. A
refusal now stops the chain. Every other error — a timeout, a DNS miss, a
JavaScript shell that rendered nothing — still earns the next backend a try.

**Firecrawl, only if you ask for it.** With `FIRECRAWL_API_KEY` set, a page
whose local fetch came back thin (a JavaScript shell, mostly) is retried through
Firecrawl. `proxy: "basic"` only — never a stealth mode — and the provider cache
is off. HTTP 402 or 429 raises `QuotaExhausted`, which stops every further
Firecrawl call for that run and keeps the successes already in hand.

**A local disk cache** at `~/.scribejay/web_fetch_cache.json`, keyed by backend
and URL, pruned of anything older than 14 days on every write. It is what makes
a backfill affordable and a re-run free. The two backends never share an entry,
so comparing local text against Firecrawl text for the same URL compares two
fetchers rather than one fetcher against itself.

Run it alone, like any other source module:

```bash
.venv/bin/python -m scribejay.sources.web_fetch https://ollama.com/blog
```

## Why summaries, and not the page text

The raw text of a fetched page is untrusted input. AGENTS.md keeps the gather
step compacting to plain fields for exactly this reason: the draft prompt's
output becomes a file in your journal folder.

So each page gets its own model call first, asking for 3–5 plain sentences
carrying what the page actually asserts — named products, versions, numbers, the
claim or the finding — and told to write `SKIP` rather than a sentence that
would be true of any page on that site. Only those sentences reach the draft. Both the fetched text **and** the finished summary are re-checked against
`learnings.excluded_keywords`, because a body is new text that the domain, title
and path filters never saw. Either one hitting drops the page.

Two prompts say so in words: the summarizer is told the page text is reference
material to describe and never instructions to follow, and the draft prompt is
told the same about `page_notes`. Neither is the real defence — the real defence
is that a summary is at most 120 words of plain prose, and a model writing that
has no file to write and no tool to call. The wording is the belt to that
suspenders.

There is a second, duller reason. `OLLAMA_NUM_CTX` defaults to **8192** tokens
and `OLLAMA_NUM_PREDICT` reserves **3072** of them for output. The existing
prompt is already around 1,500 tokens. Eight thousand characters of raw page
text is roughly 2,000 more, and Ollama trims an over-long prompt **from the
front** — taking the system prompt with it. The draft comes back missing a whole
section and nothing anywhere says why.

## Where the notes end up

The notes do two jobs, and it took a round of reading real output to see that
only one of them was happening.

**They shape the bullets.** The `page_notes` block goes into the draft prompt,
and the model compresses several pages into one bullet. That is what makes a
bullet say "over 90 organizations" instead of "reviewed industry threat
reporting" — and it is also what loses the detail.

**They are kept whole.** `journal.py:pages_read_section` appends a **Pages Read**
section to the entry: one line per page, the title linked to a scheme-validated
url, and the summary intact. Plain Python, no model call, built exactly the way
`videos_section` and `closed_tasks_section` already are. Before this the notes
were prompt input only, and nothing the summarizer wrote ever reached the vault.

It is appended **after** `has_substantive_content`, not before. That check asks
whether the *model* found anything worth logging; a Pages Read section would
answer yes on every day something was fetched, and a day of pure noise would
start producing a file.

This is also why the off-topic filter above had to land first. The draft prompt
silently drops a recipe. A Pages Read section has no judgment — it prints
whatever was fetched.

## What leaves the machine

| Setting | What goes out |
|---|---|
| `SCRIBEJAY_WEB_FETCH_ENABLED=0` | Nothing. No request, no key lookup, no extra model call. |
| Enabled, no Firecrawl key | An ordinary HTTP GET from this Mac to each chosen page, as a browser would make. |
| Enabled, Firecrawl key set | The above, **plus** the chosen URLs sent to `api.firecrawl.dev` for any page the Mac could not read, and that page's content sent back. |

Summarizing stays wherever `SCRIBEJAY_LLM_BACKEND` points — local by default,
and a cloud backend there sends the summaries out too
([docs/llm-backend.md](llm-backend.md)).

## Settings

See [docs/configuration.md](configuration.md#web-fetch--four-chrome-settings).

## Running it by hand

```bash
scribejay run daily_chrome_learnings --date 2026-08-29 --dry-run --web-fetch on
```

`--dry-run` writes no vault file and sends no email. It also logs to
`logs/daily_chrome_learnings_dryrun.log`, not the task log, so a dry run never
appears as a completed 5:15 run to `scribejay doctor` or to the dashboard that
reads that folder.

## The bake-off

Whether this is worth turning on was decided by measurement, not by argument.
Three ways of drafting the same day were compared over five days
(2026-08-13, -19, -21, -26 and -27, the five weekdays of the prior month with
the most fetchable pages):

| Arm | Fetcher | What reached the draft prompt |
|---|---|---|
| A | none | paths and titles — the behaviour with the feature off |
| B | local | local summaries — what this doc describes |
| C | Firecrawl | local summaries |

One Chrome gather and one fetch per day, shared by every arm, so the comparison
measures the arms rather than the model's run-to-run variance.

### What it actually found: the picker was the bug

The first run's page picker rejected what looked private and fetched everything
else. Over those five days it spent **10 of its 25 picks on sign-in walls** —
Yahoo Mail, Airbnb, TripIt, the Cloudflare dashboard, a hospital patient portal
— and most of the rest on receipts and search forms. Firecrawl was asked to
fetch all of them. It is not signed in as the user, so it returned login pages
rather than private data, but the urls themselves went out, and several carried
identifiers: an order id, a reservation code, an account id, an SSO `state`
token.

That is why `candidate_urls` now works as an allow list, and why `_fetch_url`
strips the query string. On the same five days the fixed picker chose **24 real
articles out of 25**. A reject list fails open; the next portal the user signs
in to is not on it.

It also invalidated the first run's headline. Firecrawl looked like the better
fetcher (20 pages to local's 12) largely by successfully rendering login
screens, which were then dropped as too thin to describe.

### The second finding: the notes were the bottleneck

Reading the three drafts side by side, they barely differed. The cause was not
the fetcher. It was everything downstream of it:

- The summarizer was capped at **60 words** and asked what the page *was about*.
  Sixty words of "about" is a label, and the tab title was already that label.
- `MAX_TEXT_PER_SUMMARY` was **3,000** against a fetcher cap of **4,000**, so a
  quarter of every fetched page was paid for and then discarded unseen.
- `MAX_PAGE_NOTES_CHARS` was **2,500**, which truncated a full day of notes.

The prompt now asks for 3–5 sentences of what the page *asserts* — named
products, versions, numbers, the claim — and says to write `SKIP` rather than a
sentence that would be true of any page on that site. The two caps were raised
to 4,000.

Re-measured on the same five days, local fetch only:

| | before | after |
|---|---|---|
| mean characters per page note | ~380 | 762 |
| mean bullet length, paths only | 201 | 201 |
| mean bullet length, with notes | ~205 | 256 |

The bullets changed in kind, not just in length. "Analyzed industry threat
reporting on how adversarial AI adoption is reshaping enterprise security"
became "CrowdStrike's 2026 Global Threat Report … affecting over 90
organizations … record-setting eCrime breakout speeds". A ClickUp help page
became the Business-Plan gate on custom default views.

**Still open.** The allow list admits any three-word headline slug, so five of
the sixteen notes that run were a Patriots signing, a late-night guest host, a
WNBA injury report, a pasta recipe and a sitcom-cast interview. The draft prompt
correctly ignored all five, but each cost a fetch and a model call.

### The numbers, after the fix

| | local | Firecrawl |
|---|---|---|
| pages fetched, of 25 | 18 | 22 |
| usable page notes | 16 | 17 |
| seconds spent fetching | 17.2 | 90.8 |

Firecrawl bought four extra pages and one extra note, for 25 credits and 5.3x
the wall clock.

| Arm | bullets | grounded | specific | note chars |
|---|---|---|---|---|
| A | 5.2 | 43.3% | 7.3% | 0 |
| B | 4.6 | 26.7% | 4.0% | 1333 |
| C | 5.2 | 41.7% | 13.3% | 1292 |

**Do not read a winner out of that table.** "Grounded" counts how often a bolded
bullet topic appears word-for-word in that arm's own source data, which
punishes accurate synthesis — "Anthropic Loop Engineering" scores zero. With
five days and about five bullets each, one bullet moves the number 20 points.
Reading the drafts side by side, A, B and C produce nearly the same text. The
automated score cannot separate them; the blind read decides.

### The one arm that did settle: raw excerpts lose

A fourth arm fed Firecrawl's page text into the draft prompt raw, at the 8,000
character budget the rejected design proposed. It is the shape the
untrusted-content rule forbids, and it ran once to measure what that rule
costs.

It cost nothing. Arm D produced the least grounded bullets of any arm **on all
five days** (17.3% mean, against 41.7% for the same text summarized) while
using four times the note budget and 39% more prompt tokens. The code path is
deleted; these numbers are the record of why.

Full report, including the blind-read procedure:
<https://claude.ai/code/artifact/db544bad-ffb6-4f2d-845c-1b8a170995a7>

## Related

- [docs/daily-learnings.md](daily-learnings.md) — the task this enriches
- [docs/model-constraints.md](model-constraints.md) — why the context budget bites
- [docs/configuration.md](configuration.md) — the settings and the Keychain
