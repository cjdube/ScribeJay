"""One command that answers "why is nothing appearing?".

`scribejay status` prints the two tables a task reads — which sources are on,
which jobs will run. That answers "what did I configure". It cannot answer the
question a user actually has at 9am when yesterday's page is missing, because
every one of the real causes lives outside those tables: consent was never
given, the model host is down, the journal folder was renamed, Full Disk
Access was revoked by an OS update, the plist is on disk but launchd never
loaded it, the run started and died half way.

So this walks the whole path from settings to written page and reports each
step as one line. Three outcomes and nothing in between:

  ok    — checked, working
  WARN  — not wrong, but the likely reason something is missing
  FAIL  — this will stop a run, and the detail says what to do

The exit code is 1 if anything FAILed, which makes `scribejay doctor` usable
from a script or a selfheal job. WARN never fails the command: a user who has
deliberately declined Strava must not get a non-zero exit for it, which is the
same rule Phase 3 applied to push alerts.

**Nothing here writes.** A health check that repaired things would hide the
fault it was run to explain, and a user is entitled to see the broken state
before anything changes it. `--probe` is the one flag that reaches the
network, and even then it only reads: it calls the same fetchers a 4:30 run
calls, through `settings_form.test_feature`, so "0 rows" here means "0 rows
tomorrow morning". `notify` is excluded from probing on purpose — testing it
means sending a real push, and a diagnostic that pings your phone is a
diagnostic people stop running.

## The Full Disk Access caveat

macOS grants Full Disk Access per *application*, and a command typed into a
terminal inherits the terminal's grant, not launchd's. So a passing FDA check
here proves your terminal can read Chrome's history — strong evidence, not
proof, that the 5:15 job can. When the check passes and the Chrome job is
still empty, the log for that job is the tiebreaker, and this prints where it
is.
"""

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from scribejay.core import config, features, registry, schema, secrets

# Statuses, in the order a summary counts them. Plain strings rather than an
# enum: they are printed, compared and counted, and one representation means
# a test asserting on output and a test asserting on a Check agree.
OK = "ok"
WARN = "WARN"
FAIL = "FAIL"
OFF = "off"      # deliberately declined — reported, never counted as a problem


@dataclass(frozen=True)
class Check:
    """One line of the report.

    `detail` is written for someone who does not know how ScribeJay works and
    wants the missing page. A FAIL's detail names the command or the file to
    change, never just the symptom.
    """

    status: str
    label: str
    detail: str = ""


# The logging formatter in core/logs.py starts every line with this stamp.
# Matched rather than the whole line parsed: the message half is free text and
# only the timestamp is structure.
_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# How much of a task log to read looking for the last run. A run is a few dozen
# lines; 200 KB covers several of them and bounds the read on a rotated file
# that has grown to megabytes.
_LOG_TAIL_BYTES = 200_000


# ---- install ----------------------------------------------------------------

def install_checks() -> list[Check]:
    path = config.config_path()
    out = [Check(OK if path.exists() else WARN, "settings file",
                 str(path) if path.exists()
                 else f"{path} does not exist — running on defaults, "
                      "`scribejay init` writes one")]

    # Parked by core/config.py at import because no logger existed yet. A task
    # run replays them into its log; a user running doctor never sees that log,
    # so they are replayed here too. Read, not popped: doctor is not the only
    # thing in this process that may want them.
    for warning in config.STARTUP_WARNINGS:
        out.append(Check(WARN, "settings", warning))

    out.append(Check(OK if secrets.available() else FAIL, "keychain",
                     f"service '{secrets.service()}'" if secrets.available()
                     else "no /usr/bin/security on this machine — credentials "
                          "cannot be stored or read"))
    return out


# ---- permissions ------------------------------------------------------------

def full_disk_access() -> Check:
    """Read one row out of Chrome's history database, the way the 5:15 job does.

    Opened `immutable=1` exactly as `sources/chrome.py` opens it, so a lock
    held by a running Chrome cannot make this report a permission problem that
    is not there. Without Full Disk Access the open itself fails — the file is
    readable to the OS and invisible to the process — which is why this runs a
    real query rather than checking `os.access`, which would answer yes.
    """
    from scribejay.sources.chrome import HISTORY_PATH

    if not HISTORY_PATH.exists():
        return Check(OFF, "full disk access",
                     "Chrome is not installed — nothing to read")
    try:
        conn = sqlite3.connect(f"file:{HISTORY_PATH}?immutable=1", uri=True)
        try:
            conn.execute("SELECT COUNT(*) FROM urls").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return Check(FAIL, "full disk access",
                     f"cannot read Chrome history ({e}). Grant it in System "
                     "Settings > Privacy & Security > Full Disk Access, to "
                     "your terminal app and to "
                     f"{sys.executable}")
    return Check(OK, "full disk access", "Chrome history is readable")


# ---- folders ----------------------------------------------------------------

def _folder(key: str, label: str, required: bool) -> Check:
    """One output folder: does it exist, and can this user write into it?

    Existence and writability are checked separately because they fail for
    different reasons and want different advice — a missing folder is usually a
    renamed vault, an unwritable one is usually a sync client holding it.
    ScribeJay never creates a journal folder (see the schema note): writing
    pages into a directory nobody reads is worse than failing loudly.
    """
    value = config.getenv(key)
    if not value:
        return Check(FAIL if required else OFF, label, f"{key} is not set")
    path = config.resolve_path(value)
    if not path.exists():
        return Check(FAIL if required else WARN, label,
                     f"{path} does not exist — create it, or point {key} "
                     "somewhere that does")
    probe = path / ".scribejay-write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError as e:
        return Check(FAIL, label, f"{path} is not writable ({e})")
    return Check(OK, label, str(path))


def folder_checks() -> list[Check]:
    return [
        _folder("LEARNINGS_DIR", "journal folder", required=True),
        _folder("CORRESPONDENCE_DIR", "correspondence folder",
                required=features.enabled("gmail")),
        _folder("SCRIBEJAY_LOGS_DIR", "log folder", required=True),
    ]


# ---- model ------------------------------------------------------------------

def model_checks() -> list[Check]:
    """Is there a model to call, and does it have the weights it names?

    Only the *default* backend is reached over the network. A per-task override
    is reported as configuration and not dialled: probing four backends to
    answer one question would make the common case slow for the rare one, and a
    task whose override is broken says so in its own log.
    """
    backend = (config.getenv("SCRIBEJAY_LLM_BACKEND") or "ollama").strip().lower()
    out = [Check(OK, "model backend", backend)]

    per_task = config.CONFIG.get("model", {}).get("per_task") or {}
    for task, value in sorted(per_task.items()):
        out.append(Check(OK, f"  {task}", f"overridden to {value}"))

    if backend == "ollama":
        out.append(_ollama_check())
    else:
        key = {"gemini": "GEMINI_API_KEY",
               "openrouter": "OPENROUTER_API_KEY"}.get(backend)
        if key is None:
            out.append(Check(FAIL, "model", f"unknown backend '{backend}' "
                                            "(expected ollama, gemini or openrouter)"))
        else:
            from scribejay.core.http import resolve_key

            out.append(Check(OK if resolve_key(key) else FAIL, "model key",
                             f"{key} is set" if resolve_key(key)
                             else f"{key} is not set — add it in `scribejay settings`"))
    return out


def _ollama_check() -> Check:
    """Reach the local model host and confirm the named model is pulled.

    Both halves matter and they fail differently: an unreachable host is
    "Ollama is not running", a reachable host missing the model is "you renamed
    the model and never pulled it", and a run that hits the second one fails
    every morning with a 404 nobody reads.
    """
    import requests

    host = config.getenv("OLLAMA_HOST")
    want = config.getenv("OLLAMA_MODEL")
    try:
        resp = requests.get(f"{host}/api/tags", timeout=5)
        resp.raise_for_status()
        names = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        return Check(FAIL, "ollama", f"{host} is not answering ({e}) — start "
                                     "Ollama, or switch backend in settings")
    # Ollama reports "llama3.1:8b" and users write "llama3.1"; a bare name
    # matches its default tag rather than being reported as missing.
    if any(n == want or n.split(":")[0] == want for n in names):
        return Check(OK, "ollama", f"{host} has {want}")
    return Check(FAIL, "ollama",
                 f"{host} is up but has no model '{want}' — run "
                 f"`ollama pull {want}` ({len(names)} model(s) installed)")


# ---- google -----------------------------------------------------------------

def google_checks() -> list[Check]:
    """The OAuth client, the token, and whether the token covers what is asked.

    The scope check is the one worth having. Adding a source to ScribeJay adds
    a scope, and an existing token minted before that has no idea: every call
    for the new source returns 403 while calendar and mail keep working, so the
    symptom is one empty page and nothing that looks like an auth problem.
    """
    from scribejay.core.google import SCOPES

    wanted = [f for f in ("google_calendar", "gmail", "youtube")
              if features.enabled(f)]
    if not wanted:
        return [Check(OFF, "google", "no Google source is switched on")]

    creds = config.resolve_path(config.getenv("GOOGLE_CREDENTIALS_PATH"))
    if not creds.exists():
        return [Check(FAIL, "google client",
                      f"{creds} is missing — see docs/setup-google.md")]
    out = [Check(OK, "google client", str(creds))]

    token = config.resolve_path(config.getenv("GOOGLE_TOKEN_PATH"))
    if not token.exists():
        out.append(Check(WARN, "google consent",
                         f"no {token} yet — the next run opens a browser once. "
                         "A launchd job cannot do that, so run "
                         "`scribejay run daily_commits` by hand first."))
        return out

    import json
    try:
        granted = set(json.loads(token.read_text()).get("scopes") or [])
    except (OSError, ValueError, TypeError) as e:
        out.append(Check(FAIL, "google consent",
                         f"{token} is unreadable ({e}) — delete it and consent again"))
        return out

    missing = [s for s in SCOPES if s not in granted]
    if missing:
        out.append(Check(FAIL, "google scopes",
                         f"consent is missing {len(missing)} scope(s): "
                         + ", ".join(s.rsplit("/", 1)[-1] for s in missing)
                         + f". Delete {token} and run a task by hand to consent again."))
    else:
        out.append(Check(OK, "google scopes", f"all {len(SCOPES)} granted"))
    return out


# ---- sources ----------------------------------------------------------------

# Probing this one sends a real push. See the module docstring.
_NO_PROBE = frozenset({"notify"})


def source_checks(probe: bool = False) -> list[Check]:
    out = []
    for feature in features.FEATURES:
        on, why = features.state(feature.name)
        if not on:
            out.append(Check(OFF, feature.name, why))
            continue
        if not probe or feature.name in _NO_PROBE:
            out.append(Check(OK, feature.name, "on"))
            continue
        out.append(_probe(feature.name))
    return out


def _probe(name: str) -> Check:
    """Call the real fetcher and report what came back.

    Every source returns `{"error": ...}` instead of raising, and
    `test_feature` turns that into a sentence — but a source can still raise
    something it never anticipated, and a doctor that crashes on the fifth of
    nine sources is worse than useless. So the call is wrapped, and an
    unexpected exception becomes this source's FAIL rather than the command's.
    """
    from scribejay.cli import settings_form

    try:
        result = settings_form.test_feature(name)
    except Exception as e:
        return Check(FAIL, name, f"raised {type(e).__name__}: {e}")
    return Check(FAIL if result.startswith("error:") else OK, name, result)


# ---- jobs -------------------------------------------------------------------

def _completion_marker(task: registry.Task) -> str:
    """The stable prefix of a task's completion line.

    `calendar_colorizer` logs "Colorizer run complete: 3 updated, 5 skipped",
    so the registry's line — which carries the zero-zero case — is not a
    substring of a real run's. Everything before the colon is, and every other
    task has no colon at all.
    """
    return task.complete_line.split(":")[0]


def last_run(task: registry.Task, logs_dir: Path) -> Check:
    """When this task last finished, read from its own log.

    Log lines, never exit codes: AGENTS.md is explicit that run history is
    built from the boundary lines, and launchd keeps no history a user can
    read. A completion line that is not the newest line in the file means the
    run after it started and never finished — which is the exact shape of the
    failure a missing page comes from, and is invisible to `schedule status`.
    """
    path = logs_dir / f"{task.key}.log"
    if not path.exists():
        return Check(WARN, task.key, f"no log at {path} — has never run")

    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - _LOG_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError as e:
        return Check(WARN, task.key, f"cannot read {path} ({e})")

    marker = _completion_marker(task)
    last_line = last_done = None
    for line in tail:
        stamp = _TIMESTAMP.match(line)
        if not stamp:
            continue                      # a traceback's continuation line
        last_line = stamp.group(1)
        if marker in line:
            last_done = stamp.group(1)

    if last_done is None:
        return Check(FAIL, task.key,
                     f"log ends at {last_line} with no completed run — see {path}")
    if last_done != last_line:
        return Check(FAIL, task.key,
                     f"last completed {last_done}, but a later run stopped at "
                     f"{last_line} without finishing — see {path}")
    return Check(OK, task.key, f"last completed {last_done}")


def job_checks() -> list[Check]:
    """Scheduling and history together, because either alone misleads.

    A loaded plist says launchd will start the job; the log says whether the
    last start produced anything. The pair is what distinguishes "never
    scheduled", "scheduled and silently failing", and "off because you said
    so".
    """
    from scribejay.cli import schedule

    logs = config.resolve_path(config.getenv("SCRIBEJAY_LOGS_DIR"))
    out = []
    for task in registry.TASKS:
        ready, why = registry.is_ready(task.key)
        if not ready:
            out.append(Check(OFF, task.key, why))
            continue

        label = schedule.label_for(task.key)
        if not schedule.plist_path(task.key).exists():
            out.append(Check(WARN, task.key,
                             "no launchd job installed — run "
                             "`scribejay schedule install`"))
            continue
        if not schedule.is_loaded(label):
            out.append(Check(FAIL, task.key,
                             f"{label} is on disk but launchd has not loaded it "
                             "— run `scribejay schedule install`"))
            continue
        out.append(last_run(task, logs))
    return out


# ---- report -----------------------------------------------------------------

SECTIONS = (
    ("INSTALL", lambda probe: install_checks()),
    ("PERMISSIONS", lambda probe: [full_disk_access()]),
    ("FOLDERS", lambda probe: folder_checks()),
    ("MODEL", lambda probe: model_checks()),
    ("GOOGLE", lambda probe: google_checks()),
    ("SOURCES", lambda probe: source_checks(probe)),
    ("JOBS", lambda probe: job_checks()),
)


def collect(probe: bool = False) -> list[tuple[str, list[Check]]]:
    """Every section's checks.

    A section that raises becomes one FAIL rather than a traceback: doctor is
    what a user runs when something is already broken, and the sections after
    the broken one carry the rest of the answer.
    """
    out = []
    for name, build in SECTIONS:
        try:
            out.append((name, build(probe)))
        except Exception as e:
            out.append((name, [Check(FAIL, name.lower(),
                                     f"check raised {type(e).__name__}: {e}")]))
    return out


def render(sections: list[tuple[str, list[Check]]]) -> list[str]:
    lines = []
    for name, checks in sections:
        lines.append(f"\n{name}")
        for check in checks:
            lines.append(f"  {check.status:5} {check.label:24} {check.detail}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scribejay doctor",
        description="Explain why a page or an event is missing.")
    parser.add_argument("--probe", action="store_true",
                        help="also call each switched-on source for yesterday's "
                             "data and report the row count (slower, uses the "
                             "network; never sends a push)")
    args = parser.parse_args(argv)

    sections = collect(probe=args.probe)
    for line in render(sections):
        print(line)

    checks = [c for _, group in sections for c in group]
    failed = sum(1 for c in checks if c.status == FAIL)
    warned = sum(1 for c in checks if c.status == WARN)
    print(f"\n{failed} failed, {warned} warning(s), {len(checks)} checked.")
    if not args.probe:
        print("Run `scribejay doctor --probe` to call each source for real.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
