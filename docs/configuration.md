# Configuration

ScribeJay resolves every setting through one function,
`scribejay/core/config.py:getenv()`, and describes every setting once, in
`scribejay/core/schema.py`. Nothing else reads the environment directly.

## The three layers

```
environment variable  ->  ~/.scribejay/config.json  ->  schema default
```

Highest wins. An empty string counts as unset at every layer, so `X=` in a
shell profile does not blank out a configured value.

**The environment is deliberately on top.** It is what `tests/conftest.py`
monkeypatches, what a launchd plist can pin, and what a power user overrides for
one run. It is also why a leftover `config/.env` silently beats everything the
settings file says — see *Migrating* below.

The schema default is the value a fresh install runs on. `config.getenv()` also
takes a caller default, used only when the schema has no row for that key.

## The settings file — `~/.scribejay/config.json`

One JSON document, sections at the top level:

```json
{
  "core":     { "timezone": "America/New_York" },
  "output":   { "learnings_dir": "~/Documents/ScribeJay" },
  "model":    { "ollama_model": "gemma4", "per_task": { "daily_commits": "gemini" } },
  "google":   { "calendar_id": "primary" },
  "persona":  { "user_name": "Robin" }
}
```

Written atomically at mode `0600`. A corrupt or unreadable file degrades to an
empty document and logs a warning rather than crashing a scheduled task.

Override the location with `SCRIBEJAY_CONFIG_DIR` (the test suite does).

`persona`, `calendar`, and `learnings` are structured rather than flat, and get
a section of their own below.

### Per-task model backend

`SCRIBEJAY_<TASK>_BACKEND` has no schema row of its own — the task list is not
fixed. It maps to `model.per_task.<task>`, lowercased:

```
SCRIBEJAY_DAILY_COMMITS_BACKEND  ->  {"model": {"per_task": {"daily_commits": "gemini"}}}
```

`SCRIBEJAY_LLM_BACKEND` has a schema row but **no default**, on purpose.
`core/model.py` treats unset as "nobody chose", logs `backend: ollama (from
unset)`, and that line is how a run says out loud that a backend was never
picked. A schema default would resolve to the same model while reporting that
someone had decided.

## Secrets live in the macOS Keychain

Seven keys never touch the settings file:

`GEMINI_API_KEY` (which also answers to `GOOGLE_API_KEY`), `OPENROUTER_API_KEY`,
`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`,
`CLICKUP_API_TOKEN`, `NTFY_TOKEN`.

They are stored as generic passwords under service `com.scribejay`, read through
`scribejay/core/secrets.py`, which shells out to the Apple-signed
`/usr/bin/security` — no new dependency, no Python keyring stack. That keeps the
settings file safe to back up, sync, and paste into a bug report.

`config.set_value()` **refuses** a secret key, so there is no accidental path
from a form field to disk.

The resolution order for a credential is
`scribejay/core/http.py:resolve_key()`:

```
explicit argument  ->  environment variable  ->  Keychain  ->  None
```

`scribejay settings` and `scribejay init` are the ordinary way to store one.
By hand, the prompt is hidden and the value never reaches a command line, so it
is never visible to `ps`:

```bash
.venv/bin/python -c 'import getpass; from scribejay.core import secrets; print(secrets.set("OPENROUTER_API_KEY", getpass.getpass("value: ")))'
```

Inspect them by hand:

```bash
security find-generic-password -s com.scribejay -a STRAVA_REFRESH_TOKEN -w
```

Google OAuth is the exception: it keeps its own token file
(`GOOGLE_TOKEN_PATH`), because that is Google's storage format, not ours.

## The schema — `scribejay/core/schema.py`

One row per setting: key, section, name, label, help, type, default, feature,
and `secret`. It is the single source of truth for four consumers that would
otherwise drift — config resolution, the migration, `scribejay init`, and the
web settings form.

`tests/test_schema.py` walks the source tree with the `ast` module, collects
every literal key passed to `config.getenv()`, and fails if one has no row. A
new setting cannot be added without describing it.

Adding a setting is one row plus one line in `config/.env.example`.

## Structured settings — `persona`, `calendar`, `learnings`

Three sections carry structure rather than a scalar: a name, a list of calendar
categories, lists of exclusions. `Setting.default` holds "the string an
environment variable would carry", and a list of eleven categories is not that,
so these live in **`schema.STRUCTURED_DEFAULTS`** instead of in a `Setting`
row. `config.section(name)` returns the user's section from
`~/.scribejay/config.json` if there is one, and the shipped default otherwise.

A section you write **replaces** the shipped one whole; it is not merged key by
key. Cut the category list to four and you have four categories, not four plus
the eleven that ship.

These defaults used to be a `preferences.example.json` file found on disk at
import. They are code now for one reason:
`scribejay/sinks/calendar.py:CATEGORY_COLORS` and
`scribejay/calendar_colorizer.py:VALID_COLOR_IDS` are computed **at import**,
so an install that could not find that file got an empty constant and a
colorizer that classified nothing — not a crash, just silence. There is no
file to fail to find any more.

### `persona`

| Key | Used by | Purpose |
|---|---|---|
| `user_name` | every task's prompt-building | The name the prompts refer to |

### `calendar`

`categories` — the table `scribejay/calendar_colorizer.py` shows the model as
its classification list, and that `scribejay/sinks/calendar.py` reads for
colorIds. Each entry:

| Field | Required | Purpose |
|---|---|---|
| `name` | yes | Category label; becomes a row in the classification table |
| `color_id` | yes | Google Calendar colorId ("1"–"11"); need not be unique |
| `color_name` | yes | Google's name for that colour (shown to the model) |
| `hint` | no | Extra classification guidance appended in the colorizer prompt |
| `role` | no | Operational tag — see below |

Several categories may share one `color_id`. Distinct names classify better
than one grab-bag category with a long `hint`.

**Roles** decouple what the code needs from what you call your categories, so
renaming "Work" to "Day job" breaks nothing. Three are read:

- `work` — the colour AI Session Time Blocks are logged with
  (`scribejay/claude_time_blocks.py`)
- `fitness` — the colour Strava activities are logged with
  (`scribejay/strava_download.py`)
- `fallback` — the colorId the colorizer uses when it cannot classify an event
  (`scribejay/calendar_colorizer.py`); expected on exactly one category

Each is looked up with `config.category_color_by_role(role, default)`, so
removing a tagged category does not crash — the task falls back to a hard-coded
colorId and keeps running. `meetings` and `appointments` appear on the shipped
categories but no code reads them; retagging or removing those changes nothing.

### `learnings`

What the daily learnings reviews (`daily_chrome_learnings`,
`daily_youtube_learnings`, `ai_chat_learnings`) ignore, read by
`scribejay/activity.py`.

`excluded_keywords` — subject matter kept out of the review entirely.
Case-insensitive substrings, matched against a browsed site's title (drops the
site) or a single page path (drops that path, keeps the site). Substring
matching is blunt on purpose, so pick distinctive terms — a short or common one
will over-match.

`excluded_domains` — domains kept out of the reviews. Matches the domain **and
its subdomains** (`sharepoint.com` covers `acme.sharepoint.com`), not a
substring (`notsharepoint.com` is kept). Ports are stripped before matching. An
empty list excludes nothing.

A service's own domain is not always enough: a Salesforce-backed portal can
serve from both `<org>.my.site.com` and `<org>.my.salesforce.com`, and both
need listing. After adding an entry, check a real day against it — see the
exclusion tests in `tests/test_activity.py`.

## Feature toggles

`SCRIBEJAY_FEATURE_<NAME>` says whether a source is used at all. Like
`SCRIBEJAY_LLM_BACKEND` it carries **no default**, because "the user has not
said" is a real third state: ScribeJay answers it by asking the machine whether
that source is even set up. See [features.md](features.md).

## Migrating an existing install

```bash
python -m scribejay.migrate --dry-run   # show what would move, no changes
python -m scribejay.migrate
```

It sorts `config/.env` into settings, Keychain secrets, per-task backends, and
anything it does not recognise; folds an old `config/preferences.json` in; and
then renames `config/.env` to `config/.env.migrated`.

`migrate` is the **only** reader of `config/preferences.json` left. Nothing
else looks for that file, so an install that still has one must run this once
or its edited categories are ignored.

The rename is not tidiness. The environment layer sits **above** the settings
file, so a `.env` left in place keeps overriding everything the settings screen
writes and the screen looks broken. Until you migrate, ScribeJay logs a warning
on every run when both files exist.

Secrets are written to the Keychain **first**, and the tool aborts before the
rename if any store fails — the only copy of a secret is never the one thrown
away. It never prints a secret value. Running it twice is a no-op.
