# Setting up Google

This is the longest thing ScribeJay asks of you, and it is worth being blunt
about why: **you have to create your own Google Cloud project.** There is no
"Sign in with Google" button to press.

That is not laziness. For ScribeJay to skip this page it would need its own
*verified* OAuth client, and `gmail.readonly` is what Google calls a
**restricted scope** — a paid third-party security assessment and a review
that takes weeks. Until then, every install uses a client the user made.

**You do not need this page to use ScribeJay.** Chrome history, AI chat
sessions and commits — the three sources that write your daily page — need no
account at all. This page buys you four more things:

| What it unlocks | Which job |
|---|---|
| Working sessions logged as calendar events | `claude_time_blocks` |
| Yesterday's events colour-coded | `calendar_colorizer` |
| Strava rides logged onto the calendar | `strava_download` |
| Yesterday's sent mail, and your YouTube Likes since the last finished day | `daily_correspondence`, `daily_youtube_learnings` |

Budget fifteen minutes. Everything is free.

> **Google renames these screens.** The consent settings have lived under
> "APIs & Services", then "OAuth consent screen", and now "Google Auth
> Platform". If a heading below does not match what you see, look for the
> nearest thing that means the same — the *order* of the steps has not
> changed in years.

---

## 1. Make a project

Go to [console.cloud.google.com](https://console.cloud.google.com). Sign in
with **the Google account whose calendar and mail you want ScribeJay to
read** — this matters at step 5, and picking the wrong one is the single most
common way this goes wrong.

Click the project dropdown in the top bar → **New Project**. Name it anything
(`scribejay` is fine). Create it, then make sure the top bar shows it as the
selected project before you carry on.

## 2. Turn on the three APIs

**APIs & Services → Library**. Search for and **Enable** each of:

- **Google Calendar API**
- **Gmail API**
- **YouTube Data API v3**

Enabling an API grants nothing by itself, so enabling all three costs nothing.
What ScribeJay may *read* is set at consent (step 7), and it asks only for the
sources you have switched on.

## 3. Fill in the consent screen

**Google Auth Platform → Branding** (older consoles: *OAuth consent screen*).

- **User type: External.** "Internal" only exists for Workspace organisations,
  and even there External is what you want for a personal tool.
- **App name**: `ScribeJay`, or anything. Only you will read it.
- **User support email** and **Developer contact**: your own address.

Skip the logo. Save.

## 4. Add yourself as a test user

**Google Auth Platform → Audience → Test users → Add users.** Add the same
Google address you signed in with.

Without this, consent in step 7 fails with "access blocked" and no useful
explanation.

### The seven-day gotcha

An app left in **Testing** status issues refresh tokens that **expire after
seven days**. ScribeJay would work for a week and then start failing every
morning with an authentication error.

So on that same **Audience** page, press **Publish app** to move the status to
*In production*. You will not be asked for a review — an unverified app in
production simply shows an extra warning screen at consent time (step 7) and
is capped at 100 users, which for a tool you built for yourself is not a cap.

If you would rather not publish, that is a legitimate choice: expect to
re-consent weekly, and `scribejay doctor` will tell you when it is time.

## 5. Create the client

**Google Auth Platform → Clients → Create client.**

- **Application type: Desktop app.** Not "Web application" — ScribeJay's
  consent flow opens a local callback port, which is what the desktop type is
  for.
- Name it anything.
- **Make a new one, even if another tool of yours already has a client in
  this project.** Google ties a grant to the user *and* the client, and
  `scribejay consent` revokes the old grant before asking again. Reuse
  another tool's client and that revoke signs the other tool out too — this
  happened on 2026-10-12, when ScribeJay still ran on a copy of Wren's
  client. A second client in the same project is fine.

Press **Create**, then **Download JSON** on the dialog that appears. You can
download it again later from the client's own page.

## 6. Put the file where ScribeJay looks

```bash
mkdir -p ~/.scribejay
mv ~/Downloads/client_secret_*.json ~/.scribejay/google_credentials.json
chmod 600 ~/.scribejay/google_credentials.json
```

That path is the default. To keep it somewhere else, set **Google credentials
file** in `scribejay settings` (the setting is `GOOGLE_CREDENTIALS_PATH`).

## 7. Consent, once, in a browser

First switch **off** any Google source you do not want, in `scribejay
settings`. A Google source you never answered counts as on once the client
file exists, and consent asks for every source that is on.

Consent needs a browser window, and **a launchd job cannot open one**. So you
run it yourself:

```bash
scribejay consent
```

It prints the scopes it will ask for before the browser opens.

A browser opens. Pick the account from step 1 — ScribeJay forces the account
chooser on purpose, because YouTube Likes only work on the account that owns
the channel.

If you published in step 4 you will see **"Google hasn't verified this app"**.
That is your own app, unverified because you did not pay for a review. Click
**Advanced → Go to ScribeJay (unsafe)**.

Approve every permission on the screen. ScribeJay writes
`~/.scribejay/google_token.json` and every later run — including the scheduled
ones — uses it silently.

**Turned a Google source on or off later?** Run `scribejay consent` again. It
revokes the old grant at Google first, then asks for the new set. Deleting the
token file is not enough: the old grant stays live at Google with nothing on
disk to show it.

## 8. Check it

```bash
scribejay doctor
```

You want three lines:

```
  ok    google client            /Users/you/.scribejay/google_credentials.json
  ok    google consent           ...
  ok    google scopes            exactly what the sources use: ...
```

Then switch the Google sources on and reinstall the schedule:

```bash
scribejay settings
scribejay schedule install
```

---

## What ScribeJay asked for, and why

One scope per switched-on source, and nothing for a source that is off
(`FEATURE_SCOPES` in `scribejay/core/google.py`):

| Scope | Asked for when | Used for |
|---|---|---|
| `calendar.events` | Google Calendar is on | Logging Strava rides and AI session blocks; recolouring yesterday's events |
| `gmail.readonly` | Gmail is on | Reading yesterday's **sent** mail — subjects and recipients only |
| `youtube.readonly` | YouTube is on | Reading your Likes |
| `gmail.send` | **Fallback email** (`BRIEF_TO_EMAIL`) is set | Emailing you a page when the vault write fails, and the colorizer's failure notice |

`calendar.events`, not `calendar`: the full scope also lets its holder delete
calendars and change who they are shared with, and ScribeJay only ever touches
events. There is no `gmail.modify`, no Google Tasks, no Pub/Sub.

A token from before this change holds the full `calendar` scope. It still
works, and `doctor` warns that it is wider than needed. Run `scribejay
consent` to narrow it.

## When it stops working

Run `scribejay doctor` first — it checks all four of these and names which one
broke.

**"consent is missing N scope(s)"** — you switched a Google source on after
consenting. Run `scribejay consent`.

**"the token holds more than the switched-on sources need"** — a WARN, not a
failure. You switched a source off, or the token predates the narrower
calendar scope. Run `scribejay consent`.

**Authentication errors starting exactly a week after setup** — the app is
still in Testing. Go back to step 4 and publish it.

**"access blocked" during consent** — you are not on the test-user list, or
you signed in with a different account than the one that owns the project.

**`google client ... is missing`** — the JSON is not where
`GOOGLE_CREDENTIALS_PATH` points. `scribejay doctor` prints the exact path it
looked at.

**The consent browser never opens, or the callback times out** — something
else is holding the callback port. Change **Google OAuth port**
(`GOOGLE_OAUTH_PORT`) in `scribejay settings`.

## Related

- [configuration.md](configuration.md) — where settings and credentials live
- [features.md](features.md) — switching the three Google sources on and off
- [cli.md](cli.md) — `scribejay doctor`, `scribejay settings`
