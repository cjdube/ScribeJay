"""`scribejay init` — the first five minutes.

The wizard's job is to get someone from `uv tool install scribejay` to a
machine that writes a page tomorrow morning, without opening a text editor and
without creating a Google Cloud project. That is possible because of what
Phase 3 calls Tier 0: Chrome history, AI chat transcripts and git commits are
all on the machine already, need no account, and are enough for a real daily
page. Everything above Tier 0 — Google, Strava, ClickUp — is offered as a link
to the settings screen rather than walked through here, because a wizard that
asks eleven questions before writing anything is a wizard people quit.

Six steps, in the order a user can answer them:

  1. what this is, and that Tier 0 needs no accounts
  2. where pages go — and this is the ONE place ScribeJay creates a folder
  3. the Tier 0 sources, plus the Full Disk Access walkthrough
  4. which model answers, and whether it is actually reachable
  5. the settings screen, for anyone who wants Tier 1 or Tier 2
  6. install the launchd jobs

## Every answer is recorded

`core/features.py` answers an unanswered toggle by probing the machine, which
is what lets an existing install upgrade without being asked anything. The
wizard is the one place that turns those guesses into recorded answers, and it
records a **no** as firmly as a yes. Someone who declines Strava during setup
should still be declining it in March when they install the Strava app for
unrelated reasons — the probe would flip that back on, and the first they would
know is a 5:50 AM push alert.

## Nothing is written until the questions are done

Answers are staged with `config.set_value` and flushed once, so quitting half
way (Ctrl-C, Ctrl-D, or a validation dead end) leaves the previous settings
exactly as they were. The two exceptions are stated where they happen: a
folder is created when it is named, and an API key goes to the Keychain when it
is typed — neither can be staged, and both are harmless to have done if the
user then quits.
"""

import getpass
import sys
from pathlib import Path

from scribejay.core import config, features, schema, secrets

BANNER = """
ScribeJay keeps the record of what actually happened.

Every morning it writes a page about your day before you wake up: the sites
you read, the AI sessions you ran, the commits you made. It can also colour
your calendar and log your Strava rides.

It runs on your Mac. By default nothing is sent to a cloud model.

The sources it starts with — browsing history, AI chat sessions, commits —
need no accounts and no sign-ups. Google, Strava and ClickUp are optional and
come later, from a settings screen.
"""

DISK_ACCESS_STEPS = """
Chrome's history file is protected by macOS. To let ScribeJay read it:

  1. Open System Settings
  2. Privacy & Security  ->  Full Disk Access
  3. Turn it on for your terminal app (Terminal, iTerm, Ghostty, ...)
  4. Quit that app COMPLETELY and open it again — the permission only
     takes effect on a fresh launch
"""


class Aborted(Exception):
    """The user quit. Nothing staged gets written."""


class Console:
    """Questions and answers, with the input side injectable.

    A wizard is a decision tree, and a decision tree is worth testing. Real
    `input()` in the middle of one means the tests either do not exist or run
    a subprocess and assert on a screen scrape. So the reading happens here,
    behind one method, and a test passes a list of answers.

    End of input is `Aborted`, never a retry loop: a wizard run with its stdin
    redirected (a script, a launchd job, a paste that ran out) must stop and
    say so rather than spin forever on EOF.
    """

    def __init__(self, answers: list[str] | None = None, out=None):
        self._answers = list(answers) if answers is not None else None
        self._out = out or sys.stdout

    def say(self, text: str = "") -> None:
        print(text, file=self._out)

    def _read(self, prompt: str) -> str:
        if self._answers is not None:
            if not self._answers:
                raise Aborted("ran out of scripted answers")
            self.say(prompt + " ")
            return self._answers.pop(0)
        try:
            return input(prompt + " ")
        except (EOFError, KeyboardInterrupt):
            raise Aborted("no more input")

    def ask(self, question: str, default: str = "") -> str:
        """A free-text answer. Enter keeps the default, which is shown."""
        suffix = f" [{default}]" if default else ""
        answer = self._read(f"{question}{suffix}").strip()
        return answer or default

    def ask_secret(self, question: str) -> str:
        """A credential the user pastes. Never echoed, never staged.

        Scripted input goes through the same list as everything else so a test
        can exercise the key path without a tty — the difference is only that
        a real terminal does not print what is typed.
        """
        if self._answers is not None:
            return self._read(question).strip()
        self.say(question)
        try:
            return getpass.getpass("  ").strip()
        except (EOFError, KeyboardInterrupt):
            raise Aborted("no more input")

    def confirm(self, question: str, default: bool = True) -> bool:
        """Yes or no. An unrecognised answer re-asks rather than guessing —
        this writes settings, and reading "nope" as yes is not recoverable by
        pressing Enter again."""
        suffix = " [Y/n]" if default else " [y/N]"
        while True:
            answer = self._read(question + suffix).strip().lower()
            if not answer:
                return default
            if answer in features.TRUE or answer in ("y", "yes"):
                return True
            if answer in features.FALSE or answer in ("n", "no"):
                return False
            self.say("Please answer y or n.")

    def choose(self, question: str, options: list[str], default: str) -> str:
        self.say(question)
        for i, option in enumerate(options, 1):
            mark = " (default)" if option == default else ""
            self.say(f"  {i}. {option}{mark}")
        while True:
            answer = self._read("Number or name:").strip().lower()
            if not answer:
                return default
            if answer in options:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.say(f"Please pick 1-{len(options)}.")


# ---- step 2: where pages go -------------------------------------------------

def _ask_folder(console: Console, key: str, question: str, default: str) -> str:
    """Ask for a folder, create it, and stage it. Re-asks on a refused path.

    Creation happens here and nowhere else in ScribeJay. `settings_form` owns
    the rule about which paths are refused (never inside /System, ~/.ssh, or
    LaunchAgents), and this passes `create=True` through it rather than
    keeping a second copy of that list — the check runs before the mkdir, so
    a typo cannot make a folder somewhere it would then refuse to write.
    """
    from scribejay.cli import settings_form

    setting = schema.get(key)
    while True:
        raw = console.ask(question, default)
        value, error = settings_form.validate_path(setting, raw, create=True)
        if error:
            console.say(f"  {error}")
            continue
        config.set_value(key, value)
        console.say(f"  using {value}")
        return value


def step_folders(console: Console) -> None:
    console.say("\n--- Where should pages go? ---")
    console.say("A plain folder or an Obsidian vault. It will be created if "
                "it does not exist.")
    journal = _ask_folder(console, "LEARNINGS_DIR", "Journal folder:",
                          config.getenv("LEARNINGS_DIR"))

    console.say("\nSent-mail summaries are written somewhere separate, so an "
                "Obsidian ingest queue does not sweep them up with the daily "
                "pages.")
    _ask_folder(console, "CORRESPONDENCE_DIR", "Correspondence folder:",
                str(Path(journal).parent / "correspondence"))


# ---- step 3: the Tier 0 sources ---------------------------------------------

# Extra questions a Tier 0 source needs before it can work. A source with no
# entry is a plain yes/no.
_FOLLOW_UP = {
    "git": ("PROJECTS_DIR", "Folder holding your git repos:"),
    "notify": ("NTFY_URL", "ntfy topic URL (https://ntfy.sh/your-topic):"),
}


def step_sources(console: Console) -> None:
    console.say("\n--- What should ScribeJay use? ---")
    console.say("All of these are already on this Mac. Nothing here needs an "
                "account.")

    for feature in features.by_tier(features.TIER_NONE):
        detected = features.configured(feature.name)
        console.say(f"\n{feature.label}")
        console.say(f"  detected: {'yes' if detected else 'no — ' + feature.setup}")
        want = console.confirm("  Use it?", default=detected)
        # Recorded either way. See the module docstring: a guess that survives
        # setup is a guess that reappears months later as an alert.
        config.set_value(features.setting_key(feature.name), "1" if want else "0")
        if not want:
            continue

        follow_up = _FOLLOW_UP.get(feature.name)
        if follow_up is None:
            continue
        key, question = follow_up
        if schema.get(key).type == "path":
            _ask_folder(console, key, "  " + question, config.getenv(key) or "")
        else:
            value = console.ask("  " + question, config.getenv(key) or "")
            if value:
                config.set_value(key, value)


def step_disk_access(console: Console) -> None:
    """Full Disk Access cannot be scripted, so this walks it and then checks.

    Checked rather than trusted, and checked by reading the database for real:
    the permission is granted to the *terminal application*, takes effect only
    on a fresh launch of it, and the failure mode is a task that runs happily
    every morning and finds no history. A user who has just clicked the
    checkbox and not relaunched will see this still fail, which is the point.
    """
    from scribejay.cli import doctor

    if not features.enabled("chrome"):
        return

    console.say("\n--- Reading Chrome history ---")
    while True:
        check = doctor.full_disk_access()
        if check.status != doctor.FAIL:
            console.say(f"  ok — {check.detail}")
            return
        console.say(DISK_ACCESS_STEPS)
        if not console.confirm("Check again?", default=True):
            console.say("  Skipped. `scribejay doctor` will tell you when it "
                        "starts working.")
            return


# ---- step 4: the model ------------------------------------------------------

_BACKENDS = ["ollama", "gemini", "openrouter"]

_BACKEND_KEYS = {"gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def step_model(console: Console) -> None:
    console.say("\n--- Which model writes the summaries? ---")
    console.say("ollama runs on this Mac and sends nothing anywhere. The other "
                "two send each task's gathered text to a cloud service.")
    backend = console.choose("Backend:", _BACKENDS,
                             config.getenv("SCRIBEJAY_LLM_BACKEND") or "ollama")
    config.set_value("SCRIBEJAY_LLM_BACKEND", backend)

    if backend == "ollama":
        from scribejay.cli import doctor

        check = doctor._ollama_check()
        console.say(f"  {check.status}: {check.detail}")
        return

    key = _BACKEND_KEYS[backend]
    console.say(f"  {backend} needs an API key. It is stored in the macOS "
                "login Keychain, never in a settings file.")
    # getpass, not ask(): a key typed into a terminal that echoes it ends up in
    # the scrollback and, on a shared screen, in a recording. Not staged either
    # — a Keychain write is its own thing and cannot go in the document.
    value = console.ask_secret(f"  Paste your {key}:")
    if not value:
        console.say("  Skipped. Add it later in `scribejay settings`.")
    elif secrets.set(key, value):
        console.say(f"  Stored {key} in the Keychain.")
    else:
        console.say(f"  Could not store {key}. Add it in `scribejay settings`.")


# ---- steps 5 and 6 ----------------------------------------------------------

def step_settings_screen(console: Console) -> None:
    console.say("\n--- Anything that needs an account ---")
    console.say("Google Calendar, Gmail and YouTube need an OAuth client you "
                "create once (docs/setup-google.md). Strava and ClickUp need "
                "one token each.")
    console.say("All of them are optional, and all of them are set from the "
                "settings screen.")
    if not console.confirm("Open the settings screen in a browser now?",
                           default=False):
        console.say("  Later: `scribejay settings`")
        return
    from scribejay.cli import settings_server

    settings_server.serve()


def step_schedule(console: Console) -> None:
    console.say("\n--- Running every morning ---")
    console.say("Only the jobs whose sources you switched on get installed. "
                "Declining a source means its job never exists, so it can "
                "never wake you with an alert.")
    if not console.confirm("Install the scheduled jobs now?", default=True):
        console.say("  Later: `scribejay schedule install`")
        return
    from scribejay.cli import schedule

    schedule.install()


# ---- the wizard -------------------------------------------------------------

def run(console: Console) -> int:
    console.say(BANNER)
    console.say(f"Settings will be written to {config.config_path()}")

    if config.config_path().exists():
        console.say("\nThat file already exists. This will update it, keeping "
                    "anything you are not asked about.")
        if not console.confirm("Continue?", default=True):
            return 1

    step_folders(console)
    step_sources(console)

    # One write, after every question that stages something. A wizard quit half
    # way must leave the old settings intact, not a document with new folders
    # and old toggles.
    config.flush()
    console.say(f"\nWrote {config.config_path()}")

    # After the flush, because both of these read the settings back: the disk
    # check asks whether chrome is switched on, and the settings screen renders
    # the document.
    step_disk_access(console)
    step_model(console)
    config.flush()

    step_settings_screen(console)
    step_schedule(console)

    console.say("\nDone. Two things worth running now:")
    console.say("  scribejay doctor        — is everything actually working?")
    console.say("  scribejay run daily_commits --date yesterday")
    console.say("\nThe first page appears tomorrow morning.")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="scribejay init",
        description="Set ScribeJay up, from nothing to a page tomorrow.")
    parser.parse_args(argv)

    console = Console()
    try:
        return run(console)
    except Aborted as e:
        print(f"\nStopped ({e}). Nothing was changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
