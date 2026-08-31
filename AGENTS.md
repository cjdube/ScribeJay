# AGENTS.md

**Canonical instructions:** This file is the sole source of project guidance.
Keep `CLAUDE.md` as the import-only compatibility pointer `@AGENTS.md`.

ScribeJay is a local-first journaling agent: it keeps the record of what
actually happened — Strava activities logged onto the calendar, yesterday's
events colour-coded after the fact, Claude/Codex working time turned into AI
Session Time Blocks, and a daily page in the journal folder built from Chrome
history, YouTube Likes, Claude/Codex/Gemini chats and the day's commits. That
folder is a plain directory or an Obsidian vault, whichever the user points at.
Nothing ships to a cloud model at runtime **by default** — an opt-in cloud
backend (Gemini, or OpenRouter for anything behind one key) is selectable
per-task via `SCRIBEJAY_LLM_BACKEND` / `SCRIBEJAY_<TASK>_BACKEND`, which does
send that task's gathered input off-device
([docs/llm-backend.md](docs/llm-backend.md)). Run `pytest` before
calling any change to existing code done.

**Origin.** ScribeJay grew out of LocalLLMAgent and became its own standalone
repo in August 2026. There is no code dependency in either direction, and no
reason for one. See [docs/architecture.md](docs/architecture.md) for the shape.

## Module map

- `scribejay/core/` — the settings/model/logging/notify/store/http/dates/
  google seam every task reads through. `core/model.py` is the one choke
  point for every model call — four tasks make one, `ai_chat_learnings` one
  per chat and `claude_time_blocks` one per block, and `strava_download` and
  `daily_correspondence` make none at all. `core/config.py` is the
  settings seam, resolving env var -> `~/.scribejay/config.json` -> the
  default in `core/schema.py`, with secrets in the macOS Keychain via
  `core/secrets.py` ([docs/configuration.md](docs/configuration.md)).
  `core/features.py` says which sources the user wants and
  `core/registry.py` maps tasks to the features they need
  ([docs/features.md](docs/features.md)).
- `scribejay/sources/` — read-only fetchers: `calendar`, `chrome`, `clickup`,
  `git`, `gmail_sent`, `strava`, `transcripts`, `youtube`.
- `scribejay/sinks/` — write-only: `calendar` (log an event, recolor one),
  `email` (the vault-write fallback and the colorizer's failure notice),
  `vault` (write a day's entry to the journal folder, or fall back to email
  if the write fails).
- `scribejay/*.py` (top level) — the eight task entrypoints, one per launchd
  job (see [docs/architecture.md](docs/architecture.md) for the schedule
  table), plus the helpers beside them: `activity.py` (exclusion-filter and
  compaction, shared by the daily learnings reviews), `correspondence.py`
  (the sent-mail noise filter and page builder), `journal.py` (deterministic
  Markdown sections), `migrate.py` (the one reader of a legacy `config/.env`
  or `preferences.json`) and `status.py`.
- `scribejay/assets/` — the mark, in SVG and three PNG sizes. The SVG is
  read by `cli/settings_form.py:load_logo()` and inlined into the settings
  page, so it ships in the wheel via `package-data` in `pyproject.toml`
  rather than sitting beside a checkout — the same reason `persona.md`
  does. A missing file renders no logo; it never fails a page.
- `scribejay/cli/` — the `scribejay` console command
  ([docs/cli.md](docs/cli.md)). `cli/schedule.py` generates the launchd plists
  from `core/registry.py`; `cli/settings_server.py` and `cli/settings_form.py`
  are the on-demand localhost settings screen, whose every field comes from
  `core/schema.py`; `cli/init.py` is the first-run wizard and `cli/doctor.py`
  the health check.

  **The generated surfaces stay generated.** `cli/settings_form.py` and
  `cli/schedule.py` derive *what exists* from `core/schema.py` and
  `core/registry.py` — never a hand-kept list, or the screen drifts from what
  the code reads. Naming a key for *presentation* is fine and already
  happens: `FILE_PATH_KEYS` and the `TIMEZONE` widget are special cases in
  the renderer, not extra fields. The two structured tabs — Event colours and
  What to leave out — have no schema rows behind them, because a structured
  section is a list, not a value. They are still derived: colours from
  `config.section("calendar")` and `schema.GOOGLE_EVENT_COLORS`, exclusions
  from `config.section("learnings")` and the `EXCLUSION_LISTS` tuple. Both
  edit a subset on purpose — a category's `name`, `hint` and `role` stay
  file-only, because one wrong `role` silently changes which colour Strava
  activities are logged with.

  `cli/init.py` and `cli/doctor.py` do name individual settings, because
  asking a question and checking a specific thing both require knowing which
  thing. Adding one there is a deliberate edit, not a violation.
- `tests/` — flat pytest suite, one `test_<module>.py` per source module.
- `config/` — legacy, and gitignored except for `.env.example`. It holds a
  pre-packaging `.env` and `preferences.json` until `migrate.py` folds them
  in, and is where an older install's Google credential and token files may
  still sit. Nothing new goes here; `~/.scribejay` is the home. `config/preferences.json` is legacy and read only by
  `migrate.py`; the shipped `persona`/`calendar`/`learnings` defaults live in
  `core/schema.py:STRUCTURED_DEFAULTS`, because `sinks/calendar.py` builds
  `CATEGORY_COLORS` from them at import and a file that fails to load there
  breaks the process rather than one call.

Each source/sink module is deliberately narrower than the equivalent it grew
from — read its own module docstring for exactly what was trimmed and why,
rather than assuming parity.

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

ScribeJay has no tool registry and no interactive turn, so there is no
model-driven write to gate. But the same untrusted text still reaches a model
that writes files: a browsed page's title, a
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
  row in `scribejay/core/registry.py` — the launchd plist is **generated** from
  that row by `scribejay/cli/schedule.py`, so do not write one. Call
  `registry.skip_if_disabled(...)`
  immediately after the opening log line — **before any gather**, or a
  declined source still gets read. **Log `Starting <name>
  run` on entry and `<name> run complete` on every success path** (and
  `logger.error` on the failure path — `notify_failure` doesn't log): a
  federating dashboard builds run history from those lines, never from exit
  codes, so a task without them reads as *has not run* and one with no
  ending hangs as *running*.
- **Persistence**: JSON stores via `scribejay/core/store.py` (`locked`,
  `load_json`, `atomic_write_json`); prune on write so polling stores don't
  grow unbounded. A store lives under `~/.scribejay` (via
  `config.resolve_path`), never beside the source tree — installed as a tool
  that would be site-packages, which a reinstall wipes.
- **Config**: read through `config.getenv()` — never `os.getenv` outside the
  seam itself. Every key needs a row in `scribejay/core/schema.py`
  (`tests/test_schema.py` walks the source and fails without one) and a line
  in `config/.env.example`. A credential gets `secret=True` and lives in the
  Keychain, never in a file. A `type="path"` value goes through
  `config.resolve_path()`, never `Path(value)`: a relative setting means
  "beside the checkout, or under `~/.scribejay`", and installed as a tool
  there is no checkout — anything defaulting beside the source tree defaults
  into site-packages.
- **Tests**: one `tests/test_<module>.py` per module; monkeypatch all
  network/model/Google collaborators; no real network calls.
- **Git**: commit straight to `main` — no feature branches.
- **Docs**: update `README.md` whenever a capability is added; detail goes
  in `docs/<name>.md` with a short linked summary in the README.

## Tests must never touch production state

The repo this split out of has three real incidents from this exact mistake: tests wrote fixture rows into production `logs/`, sent
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
