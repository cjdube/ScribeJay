# Turning sources on and off

ScribeJay reads eight kinds of material. You will not want all of them. A
source you decline must cost nothing — the job that needs it stops running
**and** stops pushing a failure alert, which is the part that matters. Before
this, a user with no Strava account still got an alert at 5:50 every morning,
forever.

## The features

| Feature | Tier | What it needs | Feeds |
|---|---|---|---|
| `chrome` | 0 | Full Disk Access | Daily Chrome learnings |
| `transcripts` | 0 | Claude Code / Codex / Gemini on this Mac | AI chat learnings, AI Session Time Blocks |
| `git` | 0 | your projects folder | Daily commits |
| `google_calendar` | 1 | your own Google OAuth client | Calendar colorizer, AI Session Time Blocks, Strava download |
| `gmail` | 1 | the same OAuth client | Daily correspondence |
| `youtube` | 1 | the same OAuth client | Daily YouTube learnings |
| `strava` | 2 | one client id, secret and refresh token | Strava download |
| `clickup` | 2 | one personal token | the ClickUp section of Daily commits |
| `notify` | 0 | an ntfy topic URL | failure alerts |

**Tier 0 is the shippable default**: a real journal — browsing, AI sessions,
commits — from a machine and no accounts at all.

The three Google features share one OAuth client but are declined separately.
Wanting the YouTube journal is not the same as consenting to ScribeJay
recolouring a work calendar.

## How ScribeJay decides

Each feature has a setting, `SCRIBEJAY_FEATURE_<NAME>`, resolved through the
usual layers ([configuration.md](configuration.md)). It has **no default**, and
that is the whole design:

- **You answered** — your answer wins, in both directions. An explicit *yes*
  is honoured even when the credentials look missing: you may know something
  the probe does not, and a task that then fails gives you a real error naming
  the real cause, which beats ScribeJay overruling you and reporting a tidy
  skip.
- **You did not answer** — ScribeJay asks the machine. Are the credentials in
  the Keychain? Does the folder exist? Is Chrome installed?

One rule, two very different people served. A stranger who just installed
ScribeJay has no Strava keys, so Strava is off and their phone stays quiet.
Someone who has run it for a year has keys in the Keychain, so Strava stays on
and nothing about their morning changes. Neither had to answer a question.

The probe is read fresh on every run, so pasting a token into the settings
screen is enough — nothing needs restarting.

Turn one off for a single run without touching any file:

```bash
SCRIBEJAY_FEATURE_STRAVA=0 python -m scribejay.strava_download
```

## What a skipped run looks like

```
2026-08-30 05:50:00 [INFO] Starting Strava download run
2026-08-30 05:50:00 [INFO] Skipping: Strava activities is turned off in settings
2026-08-30 05:50:00 [INFO] Strava download run complete
```

Both boundary lines, on purpose. A dashboard builds run history from those
lines and never from exit codes, so a skipped run that logged no ending would
show as **hung** rather than **off** — and a user who switched Strava off to
stop being nagged would get a different flavour of the same nagging.

The run exits 0 and pushes nothing. `tests/test_registry.py` asserts all three
for every task, and asserts on the source that the check runs *before* any
gather — a guard placed after the first fetch would still skip, having already
read your browsing history to do it.

## Adding a source later

One row in `scribejay/core/features.py`, one probe branch, one schema row, and
a `requires` entry on whichever task in `scribejay/core/registry.py` needs it.
The settings screen, the wizard, the generated schedule and `doctor` all read
those two tables, so nothing else has to change.
