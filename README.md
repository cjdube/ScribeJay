<img src="scribejay/assets/scribejay.svg" alt="" width="72" height="72">

# ScribeJay

ScribeJay keeps the record of what actually happened.

It runs quietly in the background on your Mac and writes down your day before
you wake up: the sites you read, the AI sessions you ran, the commits you made,
the rides you took. No chat, no prompts, no tool-calling loop — eight scheduled
jobs, each capturing one kind of activity, each writing a Markdown page or a
calendar event.

Nothing is sent to a cloud model by default.

## Install

macOS only. The schedule is launchd and the credential store is the macOS
Keychain, so there is no useful Linux build to offer yet.

Not yet on PyPI. Clone the repo, then install from the local checkout:

```bash
git clone https://github.com/cjdube/ScribeJay
uv tool install ./ScribeJay
scribejay init
```

`scribejay init` takes about five minutes. It asks where pages should go,
which of the on-machine sources to use, and which model answers; then it
installs the scheduled jobs. The first page appears tomorrow morning.

**You do not need any accounts to start.** Chrome history, AI chat transcripts
and git commits are all already on the machine, and they are enough for a real
daily page. Google, Strava and ClickUp are optional and come later.

When something looks wrong:

```bash
scribejay doctor
```

See [docs/cli.md](docs/cli.md) for every command.

## What it captures

| Job | Schedule | Writes | Needs |
|---|---|---|---|
| `ai_chat_learnings` | 4:30 AM | Yesterday's Claude Code / Codex / Gemini chats, summarized to a page | nothing |
| `claude_time_blocks` | 4:45 AM | Claude Code / Codex working sessions, as calendar events | Google |
| `daily_commits` | 4:55 AM | Yesterday's git commits across your projects, to a page | nothing |
| `daily_youtube_learnings` | 5:05 AM | Yesterday's YouTube Likes, summarized to a page | Google |
| `daily_chrome_learnings` | 5:15 AM | Yesterday's Chrome browsing, summarized to a page | nothing |
| `daily_correspondence` | 5:20 AM | Yesterday's sent email, logged to a page | Google |
| `strava_download` | 5:50 AM | Yesterday's Strava activities, as calendar events | Google + Strava |
| `calendar_colorizer` | 5:00 PM | Yesterday's uncategorized events, colour-coded by type | Google |

Each job is `gather (Python) -> model -> write` — see
[docs/architecture.md](docs/architecture.md#shape-a-pipeline-agent-not-a-tool-calling-one).
The model writes short blurbs and classifications; Python owns every date, URL
and file structure.

The middle step varies, and the ends are what define the shape. Four jobs make
one call. `ai_chat_learnings` makes one per chat and `claude_time_blocks` one
per block, each small and bounded. `strava_download` and `daily_correspondence`
make **none at all** — an activity's fields and a sent-mail header need no
sentence written about them, and asking for one would only invent detail the
source does not carry.

## Where it writes

- **A folder of Markdown** — one page per day, per job. A plain folder or an
  Obsidian vault, whichever you point it at. Falls back to email if the write
  fails.
- **Google Calendar** — events logged directly (Strava, AI session blocks) or
  recoloured in place (the colorizer).

## Sources you can decline

You will not want all eight. Decline one and the job that needs it stops
running **and** stops alerting — no more "fetch_strava failed" at 5:50 every
morning for an account you never had. Its launchd job is not installed at all.

You do not have to decide up front, either. A source with no credentials is off
and silent; one with credentials is on. `scribejay init` turns those guesses
into recorded answers, and `scribejay settings` changes them later. See
[docs/features.md](docs/features.md).

There are three tiers of what a source costs you to set up:

- **Tier 0 — nothing.** Chrome history, AI chat sessions, git commits. Already
  on the machine. Chrome needs Full Disk Access, which the wizard walks you
  through.
- **Tier 1 — your own Google Cloud project.** Calendar, sent mail, YouTube
  Likes. Fifteen minutes, free, and unavoidable: see
  [docs/setup-google.md](docs/setup-google.md) for why and how.
- **Tier 2 — one token each.** Strava, ClickUp. Paste it into
  `scribejay settings`.

## The model

The default is Ollama, running on your Mac. Nothing leaves the machine and
nothing is billed.

A cloud backend — Gemini, or OpenRouter for any frontier model behind one key
— is opt-in, and can be set per job. **Both are billed per token by the
provider**, and the jobs run unattended on a schedule, so switch one job at a
time and watch what it costs before switching the rest. See
[docs/llm-backend.md](docs/llm-backend.md).

## Where things live

| What | Where |
|---|---|
| Settings | `~/.scribejay/config.json` |
| Credentials | macOS Keychain, service `com.scribejay` |
| Logs | `~/.scribejay/logs/<task>.log` |
| launchd agents | `~/Library/LaunchAgents/local.scribejay.*.plist` |

Every setting is described once, in `scribejay/core/schema.py`, and the
settings screen is generated from it. Secrets never touch a file. See
[docs/configuration.md](docs/configuration.md).

**Upgrading an install that still has a `config/.env`:**

```bash
scribejay migrate --dry-run   # show what would move
scribejay migrate
```

## Running a job by hand

```bash
scribejay run daily_chrome_learnings
scribejay run daily_commits --date 2026-08-29
```

Arguments after the task name go to the task itself. Most jobs take none —
they do yesterday, which is the only day they are for. The four that do:

| Job | Flags |
|---|---|
| `ai_chat_learnings` | `--date`, `--backfill N` |
| `claude_time_blocks` | `--date`, `--backfill N`, `--dry-run` |
| `daily_commits` | `--date`, `--backfill N` |
| `daily_correspondence` | `--date`, `--backfill N` |

`--dry-run` exists only on `claude_time_blocks`. Ask any task for its own list
with `python -m scribejay.<task> --help`.

## Scheduling

```bash
scribejay schedule install   # only the jobs whose sources you have on
scribejay schedule status    # what is installed, and what launchd has loaded
scribejay schedule remove
```

The eight plists are **generated** from `scribejay/core/registry.py`, not
committed — so re-run `install` after changing anything in settings.

## From a source checkout

```bash
git clone <this repo>
cd ScribeJay
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scribejay.cli doctor
.venv/bin/python -m pytest
```

## Docs

- [docs/cli.md](docs/cli.md) — every command, the settings screen, the
  generated launchd jobs
- [docs/setup-google.md](docs/setup-google.md) — the one-time OAuth walkthrough
- [docs/configuration.md](docs/configuration.md) — settings, the resolution
  layers, the Keychain, and the `persona`/`calendar`/`learnings` sections
- [docs/features.md](docs/features.md) — turning sources on and off
- [docs/architecture.md](docs/architecture.md) — the pipeline shape and module
  layout
- [docs/llm-backend.md](docs/llm-backend.md) — local vs. cloud model selection
- [docs/model-constraints.md](docs/model-constraints.md) — writing prompts for
  a small local model
- [docs/timezones.md](docs/timezones.md) — UTC sources vs. local day windows
- [docs/opaque-identifiers.md](docs/opaque-identifiers.md) — never make the
  model copy an id
- [docs/ntfy-setup.md](docs/ntfy-setup.md) — the push-alert server
- [docs/logs.md](docs/logs.md) — where each job's logs live
- [docs/usage-ledger.md](docs/usage-ledger.md) — the per-model-call record of
  tokens, duration and estimated cost

One page per job, for when a page reads wrong and you want to know why:

- [docs/ai-chat-learnings.md](docs/ai-chat-learnings.md) — reading Claude Code,
  Codex and Gemini chats off the disk
- [docs/ai-session-time-blocks.md](docs/ai-session-time-blocks.md) — turning
  those same session logs into calendar blocks
- [docs/daily-commits.md](docs/daily-commits.md) — git commits and closed
  ClickUp Tasks
- [docs/daily-learnings.md](docs/daily-learnings.md) — Chrome browsing and
  YouTube Likes
- [docs/web-fetch.md](docs/web-fetch.md) — off by default: read a few of
  yesterday's pages so the Chrome review says what they said, not what their
  URL implied
- [docs/daily-correspondence.md](docs/daily-correspondence.md) — who yesterday
  was spent writing to

## Security model

ScribeJay is unattended — it has no chat interface and takes no requests, so
there is nothing to authenticate a user against. Its trust boundary is what it
*reads*: browsed page titles, sent-mail subjects, commit messages and ClickUp
task names are all untrusted text that flows into a model prompt whose output
becomes a written file. See "Untrusted content boundary" in
[AGENTS.md](AGENTS.md).

## Credit

ScribeJay grew out of [LocalLLMAgent](https://github.com/cjdube/LocalLLMAgent)
and split into its own repo in August 2026. It runs standalone; nothing is
shared but the idea.

## License

MIT.
