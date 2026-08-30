# The `scribejay` command

Installing ScribeJay gives you one command. Everything it does was reachable
before as `python -m scribejay.<something>`, and still is — the module form is
what the launchd jobs use and what every other doc's examples say.

```
scribejay init                        first-run wizard (not built yet — Phase 6)
scribejay settings                    open the settings screen in a browser
scribejay status                      what is on, what is off, what will run
scribejay run <task> [args...]        run one task now
scribejay schedule install|remove|status
scribejay doctor                      why is nothing appearing (not built yet — Phase 6)
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
- [logs.md](logs.md) — the two log streams per task
