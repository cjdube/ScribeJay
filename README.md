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
backend is the default for every job, and it is free. A cloud backend (Gemini,
or OpenRouter for any frontier model behind one key) is opt-in per job. **Both
are billed per token by the provider**, and the jobs run unattended on a
schedule, so switch one job at a time and watch what it costs before switching
the rest. See [docs/llm-backend.md](docs/llm-backend.md).

## Install

```bash
uv tool install scribejay
scribejay settings          # opens a settings page in your browser
scribejay schedule install  # schedules the jobs your sources are on for
```

macOS only — the schedule is launchd and the credential store is the macOS
Keychain. See [docs/cli.md](docs/cli.md) for every command.

From a source checkout instead:

```bash
git clone <this repo>
cd ScribeJay
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scribejay.cli status
```

Settings live in `~/.scribejay/config.json`; secrets live in the macOS
Keychain, never in a file. Every setting is described once, in
`scribejay/core/schema.py` — `config/.env.example` lists the same keys with
notes on where to get each value (Google OAuth credentials, an Ollama host,
optionally a Strava/ClickUp/Gemini/OpenRouter/ntfy key). See
[docs/configuration.md](docs/configuration.md) for the file, the layering, and
the Keychain.

Google Calendar and Gmail access need a one-time OAuth consent in a browser the
first time any job that touches them runs; `GOOGLE_OAUTH_PORT` controls the
local callback port for that flow.

Personal, non-secret settings — your name and your calendar category colors —
are the `persona`, `calendar`, and `learnings` sections of the same file. See
[docs/preferences.md](docs/preferences.md) for the schema; the defaults
shipped in `scribejay/preferences.example.json` are safe if you skip this step.

**Upgrading an existing install** that still has a `config/.env`:

```bash
scribejay migrate --dry-run   # show what would move
scribejay migrate
```

Logs moved in this release: `~/.scribejay/logs/` instead of `logs/` beside the
repo. The **file names** did not change — run history is keyed off them — and a
`SCRIBEJAY_LOGS_DIR` you had already set is still honoured.

## Optional sources

You will not want all eight sources. Decline one and the job that needs it
stops running **and** stops alerting — no more "fetch_strava failed" at 5:50
every morning for an account you never had.

You do not have to decide up front. A source with no credentials is off and
silent; one with credentials is on. Override the guess per source with
`SCRIBEJAY_FEATURE_<NAME>`. See [docs/features.md](docs/features.md).

Tier 0 — browsing, AI sessions, and commits — needs no accounts at all.

Run the test suite:

```bash
.venv/bin/python -m pytest
```

## Running a job

By hand, for testing:

```bash
scribejay run daily_chrome_learnings
scribejay run daily_commits --date 2026-08-29
```

Arguments after the task name go to the task itself — see
`python -m scribejay.<task> --help` for its flags. Most jobs support
`--dry-run` (no write).

## Scheduling

```bash
scribejay schedule install   # only the jobs whose sources you have on
scribejay schedule status    # what is installed, and what launchd has loaded
scribejay schedule remove
```

The eight plists are **generated** from `scribejay/core/registry.py`, not
committed. Turn a source off and its job is removed rather than left to skip
politely every morning, so re-run `install` after changing anything in
settings. The one committed agent is `local.scribejay.selfheal`, which repairs
the others after a Homebrew python upgrade and is installed by
`./launchd/install.sh`. See [docs/cli.md](docs/cli.md) and
[docs/logs.md](docs/logs.md).

## Docs

- [docs/cli.md](docs/cli.md) — the `scribejay` command, the settings screen,
  and the generated launchd jobs
- [docs/architecture.md](docs/architecture.md) — the pipeline shape, module
  layout, and the split's history
- [docs/configuration.md](docs/configuration.md) — settings, the resolution
  layers, and the Keychain
- [docs/features.md](docs/features.md) — turning sources on and off
- [docs/preferences.md](docs/preferences.md) — the `persona`, `calendar`, and
  `learnings` sections
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
