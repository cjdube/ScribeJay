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
  "output":   { "learnings_dir": "~/Vaults/llm-wiki-learnings" },
  "model":    { "ollama_model": "gemma4", "per_task": { "daily_commits": "gemini" } },
  "google":   { "calendar_id": "primary" },
  "persona":  { "user_name": "Robin" }
}
```

Written atomically at mode `0600`. A corrupt or unreadable file degrades to an
empty document and logs a warning rather than crashing a scheduled task.

Override the location with `SCRIBEJAY_CONFIG_DIR` (the test suite does).

`persona`, `calendar`, and `learnings` are the sections that used to live in
`config/preferences.json`. They now live here too, and this file wins where both
have the same section. See [preferences.md](preferences.md) for what those
sections mean.

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

Eight keys never touch the settings file:

`GEMINI_API_KEY` (and its `GOOGLE_API_KEY` alias), `OPENROUTER_API_KEY`,
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

Store one by hand, until `scribejay settings` exists (Phase 5). The prompt is
hidden and the value never reaches a command line, so it is never visible to
`ps`:

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
otherwise drift — config resolution, the migration, the first-run wizard, and
the web settings form.

`tests/test_schema.py` walks the source tree with the `ast` module, collects
every literal key passed to `config.getenv()`, and fails if one has no row. A
new setting cannot be added without describing it.

Adding a setting is one row plus one line in `config/.env.example`.

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
anything it does not recognise; folds `config/preferences.json` in; and then
renames `config/.env` to `config/.env.migrated`.

The rename is not tidiness. The environment layer sits **above** the settings
file, so a `.env` left in place keeps overriding everything the settings screen
writes and the screen looks broken. Until you migrate, ScribeJay logs a warning
on every run when both files exist.

Secrets are written to the Keychain **first**, and the tool aborts before the
rename if any store fails — the only copy of a secret is never the one thrown
away. It never prints a secret value. Running it twice is a no-op.
