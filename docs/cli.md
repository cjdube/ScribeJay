# The `scribejay` command

Installing ScribeJay gives you one command. Everything it does was reachable
before as `python -m scribejay.<something>`, and still is — the module form is
what the launchd jobs use and what every other doc's examples say.

```
scribejay init                        first-run wizard
scribejay settings                    open the settings screen in a browser
scribejay status                      what is on, what is off, what will run
scribejay run <task> [args...]        run one task now
scribejay schedule install|remove|status
scribejay doctor [--probe]            why is nothing appearing?
scribejay migrate [--dry-run]         move an old config/.env into the settings file
```

## Install

```bash
uv tool install scribejay
```

macOS only. The schedule is launchd and the credential store is the macOS
Keychain, so there is no useful Linux build to offer yet.

From a source checkout, everything below works the same through the venv:

```bash
.venv/bin/python -m scribejay.cli status
```

## `scribejay init`

The five-minute setup. Run it once, straight after installing:

```bash
scribejay init
```

It asks where pages should go — **the one place ScribeJay ever creates a
folder** — then walks the three sources that need no account (Chrome history,
AI chat sessions, git commits), the Full Disk Access permission, and which
model answers. Google, Strava and ClickUp are not walked through here; it
offers to open `scribejay settings` for those instead, because a wizard that
asks eleven questions before writing anything is a wizard people quit.

Two things worth knowing.

**Every answer is recorded, including "no".** Left unanswered, a source's
toggle is re-guessed from the machine on every run — which is what lets an
existing install upgrade without being asked anything. The wizard turns those
guesses into answers, so declining Strava in August still means no in March
when you install the Strava app for unrelated reasons.

**Nothing is written until the questions are done.** Answers are staged and
saved once, so quitting part way (Ctrl-C, Ctrl-D) leaves your previous settings
exactly as they were. The two exceptions: a folder is created when you name it,
and an API key goes to the Keychain when you paste it.

## `scribejay doctor`

One command for "why is yesterday's page missing?".

```bash
scribejay doctor            # local checks only, fast
scribejay doctor --probe    # also call every switched-on source for real
```

`scribejay status` prints what you *configured*. Every real cause of a missing
page lives outside that: consent was never given, Ollama is down, the vault got
renamed, an OS update revoked Full Disk Access, the plist is on disk but
launchd never loaded it, the run started and died half way. `doctor` walks the
whole path and prints one line per step:

```
GOOGLE
  ok    google client            /Users/you/.scribejay/google_credentials.json
  FAIL  google scopes            consent is missing 1 scope(s): youtube.readonly.
                                 Delete ...google_token.json and run a task by
                                 hand to consent again.

JOBS
  ok    daily_commits            last completed 2026-08-30 04:55:09
  FAIL  daily_chrome_learnings   last completed 2026-08-29 05:15:41, but a later
                                 run stopped at 2026-08-30 05:15:12 without
                                 finishing — see .../daily_chrome_learnings.log
```

Three outcomes: **ok**, **WARN** (not wrong, but the likely reason something is
missing) and **FAIL** (this stops a run, and the detail says what to do). The
exit code is 1 if anything failed, so it is usable from a script. A WARN never
fails the command — a source you deliberately declined must not produce a
non-zero exit, which is the same rule that keeps it from pushing you an alert.

Two checks are worth calling out because nothing else can make them:

- **The last run per task, read from its own log.** launchd keeps no history
  you can read, and a completion line that is not the newest line in the file
  means the run after it started and never finished.
- **Google scope coverage.** A token minted before a scope was added keeps
  calendar and mail working while the new source returns 403 — so the symptom
  is one empty page and nothing that looks like an auth problem.

`--probe` calls the same fetchers a 4:30 AM run calls and reports the row
count, so "0 rows" here means 0 rows tomorrow morning. It never sends a push:
testing the alert channel means buzzing your phone, and a diagnostic that does
that is one people stop running.

Nothing `doctor` does writes. A health check that repaired things would hide
the fault you ran it to explain.

**A note on Full Disk Access.** macOS grants it per *application*, and a
command you type inherits your terminal's grant, not launchd's. A passing check
is strong evidence the 5:15 job can read Chrome's history — not proof. When it
passes and the page is still empty, the JOBS section names the log to read.

## `scribejay run`

Arguments after the task name go to the task itself:

```bash
scribejay run daily_commits --date 2026-08-29
scribejay run daily_commits --backfill 14
```

The one exception is `--help`, which the CLI answers first. For a task's own
flags, ask the task:

```bash
python -m scribejay.daily_commits --help
```

## `scribejay schedule`

The eight launchd jobs are **generated** from `scribejay/core/registry.py`, not
committed as files. `install` writes a plist for every task whose sources you
have switched on, and removes the plist for every task you have switched off:

```bash
scribejay schedule install
scribejay schedule status
scribejay schedule remove
```

Two things follow from generating them.

**Turning a source off removes its job.** Before, a user with no Strava account
still had `local.scribejay.stravadownload` installed and skipping politely every
morning. Now it is not installed at all. Change a source in the settings screen
and re-run `scribejay schedule install` to apply it.

**The interpreter is whichever python ran the command.** launchd expands neither
`~` nor `$PATH`, so the absolute path is the only thing that works — the tool's
own venv after `uv tool install`, `.venv/bin/python` in a checkout. If you move
or rebuild the venv, re-run `install`.

`local.scribejay.selfheal` is **not** one of the generated jobs and `remove`
leaves it alone. It runs under Apple-signed `/bin/bash` so that it still starts
when a Homebrew upgrade has left the python interpreter unexecutable — which is
the exact failure it exists to repair. It is a committed file, installed by
`launchd/install.sh`, and it is only meaningful for a source checkout with a
`.venv` to heal.

## `scribejay settings`

Opens a settings page on `127.0.0.1` at a port the OS picks, opens your browser
at it, and stops when you click **Save and close** — or after 15 minutes of
inactivity, so a forgotten tab does not leave a port open. It is not a daemon;
nothing is listening once it exits.

```bash
scribejay settings                # opens the browser
scribejay settings --no-browser   # prints the URL instead
```

Every field on the page comes from `scribejay/core/schema.py` and every group
header from `scribejay/core/features.py`, so the form cannot drift out of step
with what the code actually reads. Each source has a **Test** button that calls
the real fetcher and reports the row count or the error.

Groups are a tab rail down the left, one colour each, and one panel is shown at
a time. The tabs are hidden radio buttons and CSS — no JavaScript switches
them, so the page still works with scripting off, and arrow keys move between
them. It is still **one form and one Save**: every panel is in the page whether
or not you are looking at it, and nothing is written unless every field on
every tab validates. If a save is rejected, the page reopens on the tab that
holds the rejected field and marks it.

**Credentials are write-only.** A secret field shows `set` or `not set`, never
the value. Leaving it blank means "leave it alone" — it cannot mean "clear it",
because the field renders blank on every visit and clearing on blank would
delete a working credential every time anyone pressed Save. To replace one,
paste the new value. Credentials go to the Keychain; the settings file never
holds one.

### Why a localhost port is not automatically safe

Any web page you have open can POST to `http://127.0.0.1:<port>`, and a hostile
DNS name can be pointed at `127.0.0.1` so that a page's own origin *becomes*
this server — DNS rebinding, which defeats same-origin protection entirely. So:

- binds `127.0.0.1` only, on an ephemeral port;
- a random per-launch token, in the opened URL and then in a `SameSite=Strict`
  cookie — no token, no response, not even the page;
- `Host` must be `127.0.0.1:<port>`, checked before anything reads a cookie;
- `Origin` must match on every POST, on top of the form's own CSRF token.

The token exists only in that process and the browser you are sitting at, and
the request line is never logged — a GET carries the token in its query string.

## Where things live

| What | Where |
|---|---|
| Settings | `~/.scribejay/config.json` |
| Credentials | macOS Keychain, service `com.scribejay` |
| Logs | `~/.scribejay/logs/<task>.log` |
| launchd agents | `~/Library/LaunchAgents/local.scribejay.*.plist` |

A **relative** path setting — `config/google_credentials.json` on an install
that predates packaging — still resolves against the checkout when the file is
there, and against `~/.scribejay` otherwise. Nothing an existing install has
configured moves.

## Related

- [configuration.md](configuration.md) — the settings file, the layers, the Keychain
- [features.md](features.md) — turning sources on and off
- [setup-google.md](setup-google.md) — the one-time OAuth walkthrough
- [logs.md](logs.md) — the two log streams per task
