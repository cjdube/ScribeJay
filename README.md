# ScribeJay

ScribeJay is a local-first journaling agent. It runs quietly in the
background and writes down what actually happened — no chat, no prompts, no
tool-calling loop. Eight scheduled jobs each capture one kind of activity and
either write a page to an Obsidian vault or log an event to Google Calendar.

It split out of [LocalLLMAgent](https://github.com/cjdube/LocalLLMAgent)
(Wren, an interactive agent) on 2026-08-26 and now runs standalone — no Wren
installation required. See [docs/architecture.md](docs/architecture.md) for
the split's history and the pipeline shape every task shares.

## What it captures

| Job | Schedule | Writes |
|---|---|---|
| `ai_chat_learnings` | Daily 4:30 AM | Yesterday's Claude Code / Codex Desktop / Gemini chats, summarized to a vault page |
| `claude_time_blocks` | Daily 4:45 AM | Claude Code / Codex working sessions logged as calendar events |
| `daily_commits` | Daily 4:55 AM | Yesterday's git commits across your projects, to a vault page |
| `daily_youtube_learnings` | Daily 5:05 AM | Yesterday's YouTube Likes, summarized to a vault page |
| `daily_chrome_learnings` | Daily 5:15 AM | Yesterday's Chrome browsing, summarized to a vault page |
| `daily_correspondence` | Daily 5:20 AM | Yesterday's sent email, logged to a vault page |
| `strava_download` | Daily 5:50 AM | Yesterday's Strava activities, logged as calendar events |
| `calendar_colorizer` | Daily 5:00 PM | Yesterday's uncategorized calendar events, color-coded by type |

Each job is `gather (Python) -> one model call -> write` — see
[docs/architecture.md](docs/architecture.md#shape-a-pipeline-agent-not-a-tool-calling-one).
The model writes short blurbs and classifications; Python owns every date,
URL, and file structure.

## Where it writes

- **Obsidian vault** — a Markdown page per day, per job, via
  `scribejay/sinks/vault.py`. Falls back to email if the write fails.
- **Google Calendar** — events logged directly (Strava, AI session blocks) or
  recolored in place (the colorizer), via `scribejay/sinks/calendar.py`.

Nothing ships to a cloud model at runtime by default — the local Ollama
backend is the default for every job. A cloud backend (Gemini) is opt-in per
job; see [docs/llm-backend.md](docs/llm-backend.md).

## Setup

```bash
git clone <this repo>
cd ScribeJay
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config/.env.example config/.env          # fill in your own values
cp config/preferences.example.json config/preferences.json   # optional, for calendar categories
```

Fill in `config/.env` — see the comments in `config/.env.example` for what
each variable is and where to get it (Google OAuth credentials, an Ollama
host, optionally a Strava/ClickUp/Gemini/ntfy key). Google Calendar and Gmail
access need a one-time OAuth consent in a browser the first time any job that
touches them runs; `GOOGLE_OAUTH_PORT` in `.env` controls the local callback
port for that flow.

`config/preferences.json` holds personal, non-secret settings — your name
and your calendar category colors. See
[docs/preferences.md](docs/preferences.md) for the schema; the committed
`preferences.example.json` is a safe default if you skip this step.

Run the test suite:

```bash
.venv/bin/python -m pytest
```

## Running a job

By hand, for testing:

```bash
.venv/bin/python -m scribejay.daily_chrome_learnings
```

Most jobs support `--dry-run` (no write) — check the module's `main()` for
its flags.

## Scheduling

```bash
./launchd/install.sh              # installs all 8 agents
launchctl list | grep scribejay   # check status
launchctl kickstart -k gui/$(id -u)/local.scribejay.dailycommits   # run one now
```

Re-running `install.sh` is safe — it boots out and reinstalls. After editing
a `.plist`, re-run `install.sh` (or just that one plist's path) to pick up
the change. See [docs/logs.md](docs/logs.md) for where each job's output
lands.

## Docs

- [docs/architecture.md](docs/architecture.md) — the pipeline shape, module
  layout, and the split's history
- [docs/preferences.md](docs/preferences.md) — `config/preferences.json` schema
- [docs/llm-backend.md](docs/llm-backend.md) — local vs. cloud model selection
- [docs/model-constraints.md](docs/model-constraints.md) — writing prompts for
  a small local model
- [docs/timezones.md](docs/timezones.md) — UTC sources vs. local day windows
- [docs/opaque-identifiers.md](docs/opaque-identifiers.md) — never make the
  model copy an id
- [docs/ntfy-setup.md](docs/ntfy-setup.md) — the push-alert server
- [docs/logs.md](docs/logs.md) — where each job's logs live

## Security model

ScribeJay is unattended — it has no chat interface and takes no requests, so
there's nothing to authenticate a user against. Its trust boundary is what it
*reads*: browsed page titles, sent-mail subjects, commit messages, and
ClickUp task names are all untrusted text that flows into a model prompt
whose output becomes a written file. See "Untrusted content boundary" in
[AGENTS.md](AGENTS.md).

## License

MIT.
