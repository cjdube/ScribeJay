# Personal preferences — the `persona`, `calendar`, and `learnings` sections

*Split from LocalLLMAgent's `docs/preferences.md` — that doc also covers keys
ScribeJay's code never reads (`job_search`, `morning_brief`, `sports`,
`location`, `projects.instruction_files`). This is ScribeJay's own half:
just the keys `scribejay/core/config.py` actually consumes.*

**These three sections now live in `~/.scribejay/config.json`** alongside every
other setting — see [configuration.md](configuration.md) for the file, the
layering, and the Keychain. `config/preferences.json` is still read as a
fallback for installs that have not migrated yet; where both files carry a
section, `~/.scribejay/config.json` wins. `python -m scribejay.migrate` folds
the old file in.

ScribeJay separates three kinds of configuration:

- **Secrets** (API keys, tokens) — the macOS Keychain, never a file on disk.
- **Personal preferences** (who ScribeJay serves and how events are
  categorized) — the sections below. Not secret, just personal.
- **Everything else** — plain settings, described once in
  `scribejay/core/schema.py`.

The preferences sections are loaded once at import by
`scribejay/core/config.py`, which falls back to the packaged
`scribejay/preferences.example.json` when
you have not made your own copy — so a fresh clone boots with a valid schema.
A file that exists but is unparseable degrades to `{}` (nothing crashes), but
every consumer below then runs with generic/empty values — several of them
(`scribejay/sinks/calendar.py`'s `CATEGORY_COLORS`,
`scribejay/calendar_colorizer.py`'s `VALID_COLOR_IDS`) compute a module-level
constant from this file at import time, so a missing category breaks that
constant for the whole process, not just one call. Keep it valid.
`tests/test_config.py` guards the schema of whichever file is live.

## Keys

### `persona`

| Key | Used by | Purpose |
|---|---|---|
| `user_name` | every task's prompt-building | The name the LLM prompts refer to |

### `calendar`

`categories` — the list `scribejay/calendar_colorizer.py` shows the model as
its classification table, and that `scribejay/sinks/calendar.py` reads for
colorIds. Each entry:

| Field | Required | Purpose |
|---|---|---|
| `name` | yes | Category label; becomes a row in the classification table |
| `color_id` | yes | Google Calendar colorId ("1"–"11"); need not be unique |
| `color_name` | yes | Google's name for that color (shown to the model) |
| `hint` | no | Extra classification guidance appended in the colorizer prompt |
| `role` | no | Operational tag — see below |

Several categories may share one `color_id`. Distinct names classify better
than one grab-bag category with a long `hint`.

**Roles** decouple what the code needs from what you call your categories.
ScribeJay reads exactly two:

- `fitness` — the color Strava activities are logged with
  (`scribejay/strava_download.py`)
- `fallback` — the colorId the colorizer uses when it can't classify an event
  (`scribejay/calendar_colorizer.py`); expected on exactly one category

`work`, `meetings`, `appointments` may appear on categories (LocalLLMAgent's
schema uses them), but no ScribeJay code reads them — retagging or removing
them changes no behavior here.

### `learnings`

What the daily learnings reviews (`daily_chrome_learnings`,
`daily_youtube_learnings`, `ai_chat_learnings`) ignore, read by
`scribejay/activity.py`.

`excluded_keywords` — subject matter kept out of the review entirely.
Case-insensitive substrings, matched against a browsed site's title (drops
the site) or a single page path (drops that path, keeps the site). Substring
matching is blunt on purpose, so pick distinctive terms — a short or common
term will over-match.

`excluded_domains` — domains kept out of the daily learnings reviews.
Matches the domain **and its subdomains** (`sharepoint.com` covers
`acme.sharepoint.com`), not a substring (`notsharepoint.com` is kept). Ports
are stripped before matching. Empty list = nothing excluded.

A service's own domain isn't always enough: a Salesforce-backed portal can
serve from both `<org>.my.site.com` and `<org>.my.salesforce.com`, and both
need listing. After adding an entry, check a real day against it (see the
exclusion tests in `tests/test_activity.py`).

## Not read by ScribeJay

`projects`, `morning_brief`, `sports`, `location`, and `job_search` are
LocalLLMAgent-only keys — no ScribeJay module reads them. They may still
appear in `scribejay/preferences.example.json` for schema parity with the sibling
repo's file, but leaving them out costs nothing here.
