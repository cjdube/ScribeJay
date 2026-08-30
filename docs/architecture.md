# ScribeJay — the journaling agent

ScribeJay keeps the record of what actually happened. It started life inside the
Wren repo (LocalLLMAgent), split out on 2026-08-26 after a demo where the
audience said out loud what the code already showed: a good part of Wren was
journaling, which is a different job from being an interactive agent. It became
its own standalone repo shortly after, so it can run with no Wren installed.

Code: `scribejay/`. Charter: the `scribejay/__init__.py` docstring — read it
before adding anything here.

## The seam

**ScribeJay writes the record, Wren reads it.**

Wren is the interactive agent: she reads the record — through the calendar and
the wiki — and takes action on request (book a meeting, set a reminder, send the
brief). ScribeJay never talks to anyone; it runs unattended under launchd and
leaves calendar events and vault pages behind.

`tasks/daily_synthesis.py` is deliberately **not** journaling and stays with
Wren. Journaling is "write down what was done"; synthesis applies yesterday's
activity to notes and projects, which is reasoning.

## Shape: a pipeline agent, not a tool-calling one

Every ScribeJay task is the same three steps:

```
gather (deterministic Python)  ->  one complete_text() call  ->  write
```

There is no tool registry and no `advance()`-style tool-calling loop. The
model writes blurbs and scores; Python owns dates, structure, URLs and file
assembly. `scribejay/core/model.py` is the single choke point for that one
model call per task — no confirm gates, no cancel path, no busy-slot probing,
because there is no interactive turn to protect.

**The middle step can be zero calls.** `scribejay/strava_download.py` and
`scribejay/daily_correspondence.py` are pure field mapping — a Strava activity
onto a calendar event, a sent-mail header onto a line — with no natural-language
step to hand the model. Adding one would only let it invent detail the source
does not carry. Gather and write are the two steps that define the shape; the
model call is what most tasks need, not what makes it a ScribeJay task.

## The tasks

| Task | Schedule | launchd label |
|---|---|---|
| `scribejay/ai_chat_learnings.py` | Daily 4:30 AM | `local.scribejay.aichatlearnings` |
| `scribejay/claude_time_blocks.py` | Daily 4:45 AM | `local.scribejay.claudetimeblocks` |
| `scribejay/daily_commits.py` | Daily 4:55 AM | `local.scribejay.dailycommits` |
| `scribejay/daily_youtube_learnings.py` | Daily 5:05 AM | `local.scribejay.dailyyoutubelearnings` |
| `scribejay/daily_chrome_learnings.py` | Daily 5:15 AM | `local.scribejay.dailychromelearnings` |
| `scribejay/daily_correspondence.py` | Daily 5:20 AM | `local.scribejay.dailycorrespondence` |
| `scribejay/strava_download.py` | Daily 5:50 AM | `local.scribejay.stravadownload` |
| `scribejay/calendar_colorizer.py` | Daily 5:00 PM | `local.scribejay.calendarcolorizer` |

Plus two helpers: `scribejay/journal.py` (the video-list section and the
"is this draft substantive?" check) and `scribejay/sources/transcripts.py`
(Claude Code and Codex Desktop session readers plus the Gemini drop-folder
reader, both shared by `ai_chat_learnings` and `claude_time_blocks`).

**The module basenames did not change during the split, on purpose.** A
dashboard reading a task's run history off its log file (`chat/insights.py`
in Wren's case) derives the task's key from the log basename, not the launchd
label — keeping `logs/strava_download.log` et al. identical is what lets a
dashboard reading both repos keep a task's full run history through the move.

## Module layout

- `scribejay/core/` — the settings/model/logging/notify/store/http/dates/
  google seam every task reads through. `core/model.py` is the one choke
  point for the model call; `core/config.py` is the settings seam (env vars
  plus `config/preferences.json`, gitignored, falling back to the committed
  `config/preferences.example.json`).
- `scribejay/sources/` — read-only fetchers: `calendar`, `chrome`, `clickup`,
  `git`, `gmail_sent`, `strava`, `transcripts`, `youtube`.
- `scribejay/sinks/` — write-only: `calendar` (log an event, recolor one),
  `email` (the vault-write fallback and the colorizer's failure notice),
  `vault` (write a day's entry to the Obsidian vault, or fall back to email
  if the write fails).
- `scribejay/*.py` (top level) — the task entrypoints themselves, one per
  launchd job, plus `activity.py` (the exclusion-filter and compaction
  helpers shared by the daily learnings reviews).

Each source/sink module is a narrower slice of what a Wren equivalent once
covered — read its own module docstring for exactly what was trimmed and why.

## Model backend

ScribeJay resolves its own backend in `scribejay/core/model.py`:

```
SCRIBEJAY_<TASK_KEY>_BACKEND  ->  SCRIBEJAY_LLM_BACKEND  ->  ollama
```

Task keys match the `scribejay/` module names. There is deliberately **no**
fallback to any Wren-style `WREN_*` variable — a silent fallback there would
hide a missed `.env` setup. Every run logs which backend it resolved to and
where that came from, because the failure mode is silent: an unset variable
is not an error, just a smaller model and a thinner draft.

A future OpenRouter backend is one `.env` line and no code change in
`scribejay/`.

## Running one by hand

```bash
.venv/bin/python -m scribejay.claude_time_blocks --dry-run
```

```bash
.venv/bin/python -m scribejay.daily_chrome_learnings
```
