# AGENTS.md

**Canonical instructions:** This file is the sole source of project guidance.
Keep `CLAUDE.md` as the import-only compatibility pointer `@AGENTS.md`.

ScribeJay is a local-first journaling agent: it keeps the record of what
actually happened — Strava activities logged onto the calendar, yesterday's
events colour-coded after the fact, Claude/Codex working time turned into AI
Session Time Blocks, and a daily page in the Obsidian vault built from Chrome
history, YouTube Likes, Claude/Codex/Gemini chats and the day's commits.
Nothing ships to a cloud model at runtime **by default** — an opt-in cloud
backend (Gemini) is selectable per-task via `SCRIBEJAY_LLM_BACKEND` /
`SCRIBEJAY_<TASK>_BACKEND`, which does send that task's gathered input
off-device ([docs/llm-backend.md](docs/llm-backend.md)). Run `pytest` before
calling any change to existing code done.

**Sibling repo.** ScribeJay split out of LocalLLMAgent (Wren, the interactive
agent) on 2026-08-26 and became its own standalone repo shortly after — it
runs with no Wren installed. The seam is one sentence — **ScribeJay writes the
record, Wren reads it** — through the calendar and the Obsidian vault; there
is no code dependency in either direction. See
[docs/architecture.md](docs/architecture.md) for the full history and shape.

## Module map

- `scribejay/core/` — the settings/model/logging/notify/store/http/dates/
  google seam every task reads through. `core/model.py` is the one choke
  point for the single model call a task makes; `core/config.py` is the
  settings seam (env vars plus `config/preferences.json`, gitignored,
  falling back to the committed `config/preferences.example.json`).
- `scribejay/sources/` — read-only fetchers: `calendar`, `chrome`, `clickup`,
  `git`, `gmail_sent`, `strava`, `transcripts`, `youtube`.
- `scribejay/sinks/` — write-only: `calendar` (log an event, recolor one),
  `email` (the vault-write fallback and the colorizer's failure notice),
  `vault` (write a day's entry to the Obsidian vault, or fall back to email
  if the write fails).
- `scribejay/*.py` (top level) — the eight task entrypoints, one per launchd
  job (see [docs/architecture.md](docs/architecture.md) for the schedule
  table), plus `activity.py` (exclusion-filter and compaction helpers shared
  by the daily learnings reviews) and `journal.py` /
  `sources/transcripts.py` (shared helpers).
- `tests/` — flat pytest suite, one `test_<module>.py` per source module.
- `config/` — `.env` (documented in `.env.example`) plus
  `preferences.json` (documented in `.example.json`), both gitignored.

Each source/sink module is deliberately narrower than any Wren equivalent it
grew from — read its own module docstring for exactly what was trimmed and
why, rather than assuming parity.

## Data sourcing policy

Scrapers and enrichment SaaS are tempting when adding data-driven capture.
Don't.

- **Only ToS-clean sources.** Official APIs, public JSON/RSS endpoints, and
  data a service deliberately sends. Never scrape a site that prohibits it,
  and never route around it through scraping SaaS — a banned account costs
  more than the signal.
- **No paid SaaS dependencies for data.** Prefer free/official sources. A
  subscription contradicts the local-first design; flag it for discussion.
- **If a signal has no legitimate source, say so** and drop or defer it —
  don't quietly substitute a gray-area source.
- **Every HTTP call has an explicit timeout.**
- **Degrade, don't crash.** A failing source returns `{"error": ...}` and
  reads as empty to callers; one dead source must never crash a task.
- **A source's timestamps are UTC until proven otherwise; day windows are
  local.** Convert with `local_timezone()` from `scribejay/core/dates.py`;
  never slice an ISO stamp against a local day. Tests here pin `TIMEZONE`
  and use an evening timestamp — the two dates agree until 8pm, which is how
  this class of bug survives review. [docs/timezones.md](docs/timezones.md)

## Untrusted content boundary

ScribeJay has no tool registry and no interactive turn, so there's no
model-driven write to gate the way Wren gates hers. But the same untrusted
text still reaches a model that writes files: a browsed page's title, a
sent-mail subject, a commit message, a ClickUp task name all flow into a
prompt whose output becomes a vault page or a calendar event. Keep the
gather step compacting to plain fields (title, url, count) rather than
passing raw page bodies, and never let a source's content control which
file gets written or where.

## Small-local-model constraints

The default backend is a small on-device model; design around it:

- **Deterministic Python owns structure.** Dates, durations, timestamps,
  URLs, and file assembly happen in Python — never ask the model for a date
  or let it freehand a whole page. [docs/model-constraints.md](docs/model-constraints.md)
- **The model writes blurbs and scores, not documents.** Compact the input to
  bound the prompt, request a line-oriented output format, and parse
  defensively.
- **Scheme-validate any URL** before rendering it into Markdown or HTML —
  `from scribejay.core.urls import safe_url`, don't copy it.
- **Pass `think=False` for any call that fills in a template** — a
  classification, a score, a fixed output format — and pass `logger=` with
  it. Thinking tokens share the `num_predict` budget, so over-reasoning
  returns *empty content*, not a truncated answer. Leave it on only where
  the model must reason past the prompt, and measure even then.
  [docs/model-constraints.md](docs/model-constraints.md)
- **Degrading on bad model output is only safe if it's logged.** If a parse
  yields *fewer* results than inputs — not just zero — log WARNING with the
  counts and the raw length. A task that silently produces *less* pushes no
  alert, while a failing one does.
- **Never make the model copy an opaque identifier.** Number the items
  (`{"n": 1, ...}`) and map back to ids in Python.
  [docs/opaque-identifiers.md](docs/opaque-identifiers.md)

## Conventions quick reference

- **New scheduled task**: `scribejay/<name>.py` with `main() -> int`,
  `setup_logger` and `notify_failure` from `scribejay/core/logs.py`, plus a
  launchd plist (`local.scribejay.*`) in `launchd/`. **Log `Starting <name>
  run` on entry and `<name> run complete` on every success path** (and
  `logger.error` on the failure path — `notify_failure` doesn't log): a
  federating dashboard builds run history from those lines, never from exit
  codes, so a task without them reads as *has not run* and one with no
  ending hangs as *running*.
- **Persistence**: JSON stores under `config/` via `scribejay/core/store.py`
  (`locked`, `load_json`, `atomic_write_json`); prune on write so polling
  stores don't grow unbounded.
- **Config**: `os.getenv()` / `config.getenv()` with inline defaults;
  document every new variable in `config/.env.example`.
- **Tests**: one `tests/test_<module>.py` per module; monkeypatch all
  network/model/Google collaborators; no real network calls.
- **Git**: commit straight to `main` — no feature branches.
- **Docs**: update `README.md` whenever a capability is added; detail goes
  in `docs/<name>.md` with a short linked summary in the README.

## Tests must never touch production state

Wren's history (the repo this split out of) has three real incidents from
this exact mistake: tests wrote fixture rows into production `logs/`, sent
real ntfy pushes, and a daemon thread outlived its test and overwrote a
production store. The same rules apply here, enforced by autouse fixtures in
`tests/conftest.py`:

- **Never spawn a real background thread in a test.** Any function that
  spawns one gets an autouse stub in its test file. A surviving thread
  resolves monkeypatched paths *after* they're restored — a passing suite is
  timing luck.
- **Every production side effect gets a suite-wide guard in
  `tests/conftest.py`**, not just per-test monkeypatching: JSON stores under
  `config/` → redirect to `tmp_path`; `logs/` → redirect; push/email/ClickUp
  calls → stub.
- **Adding a new store, log, push channel, or thread-spawner? Extend
  `tests/conftest.py` in the same commit.** Per-test isolation is the
  convention; the conftest guard is the backstop that makes a missed
  convention harmless.
