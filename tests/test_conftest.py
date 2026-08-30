"""Guards on the suite-wide isolation in conftest.py.

Every fixture in conftest.py is invisible: nothing fails when one regresses.
The suite just quietly starts writing into the user's real logs, real vault,
or real repositories — which is exactly how the incidents behind those
fixtures happened in the repo ScribeJay split out of. So assert each guard
directly rather than trusting that the fixture is still there.

Each test below names the production thing it is protecting.
"""

import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from scribejay import ai_chat_learnings, correspondence
from scribejay.core import logs as _logs
from scribejay.sinks import vault
from scribejay.sources import git as git_source
from scribejay.sources import transcripts

# Derived from the source tree, not from conftest, so this can't agree with a
# broken redirect by sharing its mistake.
_REAL_LOGS_DIR = Path(_logs.__file__).resolve().parent.parent.parent / "logs"


def _file_handlers():
    loggers = [logging.getLogger()]
    loggers += [
        lg
        for lg in logging.Logger.manager.loggerDict.values()
        if isinstance(lg, logging.Logger)
    ]
    return [
        (lg.name, h)
        for lg in loggers
        for h in lg.handlers
        if isinstance(h, logging.FileHandler)
    ]


# --------------------------------------------------------------------------- #
# logs/ — every task calls setup_logger
# --------------------------------------------------------------------------- #

def test_logs_dir_is_redirected_away_from_production():
    assert _logs.LOGS_DIR != _REAL_LOGS_DIR


def test_no_log_handler_writes_into_the_real_logs_dir():
    escaped = [
        f"{name} -> {h.baseFilename}"
        for name, h in _file_handlers()
        if Path(h.baseFilename).resolve().parent == _REAL_LOGS_DIR
    ]
    assert not escaped, (
        "log handlers are bound to the production logs/ dir: "
        + "; ".join(escaped)
        + " — these append fixture rows to real logs on every pytest run"
    )


def test_a_child_interpreter_inherits_the_logs_redirect():
    # A parent-side monkeypatch cannot reach a child process, so the redirect
    # has to travel via the environment. Nothing spawns a child today; this
    # pins the mechanism before something does.
    code = "\n".join([
        "from scribejay.core.logs import setup_logger",
        "lg = setup_logger('conftest_guard_probe')",
        "print(lg.handlers[0].baseFilename)",
    ])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    child_log = Path(proc.stdout.strip().splitlines()[-1]).resolve()
    assert child_log.parent != _REAL_LOGS_DIR, (
        f"a child interpreter wrote {child_log} — SCRIBEJAY_LOGS_DIR is not "
        "reaching subprocesses"
    )


def test_a_handler_on_the_real_logs_dir_is_refused():
    # The backstop behind the redirect: it moves the path, this refuses the
    # write. Without it, a redirect's absence is silent. Build the handler
    # setup_logger builds, at the path it would have used.
    with pytest.raises(RuntimeError, match="production log"):
        RotatingFileHandler(_REAL_LOGS_DIR / "daily_commits.log",
                            maxBytes=2_000_000, backupCount=3)


def test_the_refusal_covers_plain_file_handlers_too():
    # Patched onto FileHandler rather than RotatingFileHandler so a future
    # module reaching for logging's plain handler is covered without a code
    # change.
    with pytest.raises(RuntimeError, match="production log"):
        logging.FileHandler(_REAL_LOGS_DIR / "anything.log")


def test_the_refusal_leaves_handlers_outside_the_real_logs_dir_alone(tmp_path):
    # The block must be narrow: every task logger in the suite builds a handler
    # under tmp_path, and they all have to keep working.
    handler = RotatingFileHandler(tmp_path / "scribejay.log")
    handler.close()


# --------------------------------------------------------------------------- #
# The vault — the user's real Obsidian notes
# --------------------------------------------------------------------------- #

def test_learnings_dir_is_redirected_away_from_the_real_vault():
    assert vault._learnings_dir() != Path(vault.DEFAULT_LEARNINGS_DIR).expanduser(), \
        "LEARNINGS_DIR still resolves to the user's real Obsidian vault"


def test_correspondence_dir_is_redirected_away_from_the_real_vault():
    # A second real vault path, deliberately kept out of the ingest queue.
    assert correspondence._correspondence_dir() != \
        Path(correspondence.DEFAULT_CORRESPONDENCE_DIR).expanduser(), \
        "CORRESPONDENCE_DIR still resolves to the user's real vault"


# --------------------------------------------------------------------------- #
# The machine — paths that read the developer's own disk
# --------------------------------------------------------------------------- #

def test_projects_dir_is_redirected_away_from_the_real_checkouts():
    # sources/git.py walks PROJECTS_DIR and shells out to git for every
    # checkout under it. fetch_repos() then runs `git fetch --all` on each —
    # network egress AND a write into the .git dir of the user's real
    # repositories, this one among them. The single most consequential
    # redirect in this file.
    resolved = git_source._projects_dir()
    assert resolved != Path(git_source.DEFAULT_PROJECTS_DIR).expanduser(), \
        f"PROJECTS_DIR resolves to the developer's real checkouts ({resolved}) — " \
        "an unstubbed fetch_repos() would git-fetch every repo there"


def test_the_pinned_projects_dir_holds_no_repositories():
    # The redirect is only worth as much as the dir it points at: an empty tree
    # means _repos() finds nothing, so fetch_repos() cannot reach a remote even
    # if a test calls it unstubbed.
    assert not list(git_source._projects_dir().glob("*/.git"))


def test_chat_transcript_sources_are_redirected_away_from_real_history():
    # ~/.claude and ~/.codex hold the user's actual session transcripts.
    assert transcripts.CLAUDE_PROJECTS_DIR != transcripts.claude_projects_dir()
    assert transcripts.CODEX_SESSIONS_DIR != transcripts.codex_sessions_dir()


def test_the_ai_chat_learnings_store_is_redirected_away_from_config():
    real_config = Path(_logs.__file__).resolve().parent.parent.parent / "config"
    assert Path(ai_chat_learnings.STATE_PATH).resolve().parent != real_config, \
        "a test writing this store would move the production dedup watermark"


# --------------------------------------------------------------------------- #
# Network egress — none of it may reach a real service
# --------------------------------------------------------------------------- #

def test_ntfy_egress_is_stubbed_for_both_verbs():
    # notify() POSTs a push at the user's phone; the health probe GETs the live
    # server. A regression here is silent — a real push, a real probe — so
    # assert it rather than trusting the fixture's presence.
    from scribejay.core import notify as notify_mod

    for verb in ("post", "get"):
        fn = getattr(notify_mod.requests, verb)
        assert fn.__module__ != "requests.api", (
            f"scribejay.core.notify.requests.{verb} is the real requests "
            "function — conftest's _block_ntfy_egress is not in effect, and the "
            "suite can reach the user's actual ntfy server"
        )


def test_clickup_egress_raises_rather_than_degrading():
    # closed_tasks() ends in `except Exception: return http_error(e)`, so a
    # guard raising an ordinary Exception would be swallowed by the very code
    # it guards and the test would pass having proved nothing. Assert the
    # guard reaches the runner.
    from scribejay.sources import clickup

    with pytest.raises(BaseException) as excinfo:
        clickup._get("/team")
    assert not isinstance(excinfo.value, Exception), \
        "the ClickUp guard is a plain Exception — closed_tasks() will swallow it"


def test_gemini_client_is_blocked():
    from scribejay.core.backends import gemini

    with pytest.raises(RuntimeError, match="Gemini"):
        gemini._gemini_client()


def test_real_email_send_is_blocked():
    from scribejay.sinks import email

    with pytest.raises(RuntimeError, match="Gmail"):
        email.send_email(subject="probe", body="probe")


# --------------------------------------------------------------------------- #
# Settings — the file and the Keychain the whole suite resolves through
# --------------------------------------------------------------------------- #

def test_the_settings_file_is_redirected_away_from_the_users_own():
    from scribejay.core import config

    assert config.config_dir() != Path.home() / ".scribejay", \
        "the suite is reading and writing the user's real settings file"


def test_the_env_file_is_redirected_and_does_not_exist():
    # config/.env is the TOP resolution layer, so an un-redirected one silently
    # decides every setting the suite sees — a fresh clone and a configured
    # machine would then disagree about what a green suite proves.
    from scribejay.core import config

    resolved = config.env_path()
    real = Path(config.__file__).resolve().parent.parent.parent / "config" / ".env"
    assert resolved != real, "the suite is reading the developer's real config/.env"
    assert not resolved.exists(), \
        f"the redirected .env at {resolved} exists — tests must resolve off defaults"


def test_a_real_setting_resolves_to_its_schema_default():
    # The end-to-end proof of the two redirects above: with both in effect, a
    # setting the developer has customised must come back as the shipped
    # default, not as their value.
    from scribejay.core import config, schema

    assert config.getenv("OLLAMA_MODEL") == schema.default_for("OLLAMA_MODEL")


def test_keychain_writes_raise_rather_than_degrading():
    # core/secrets.py catches OSError/SubprocessError to degrade on a missing
    # `security` binary, so a guard raising an ordinary Exception would be
    # swallowed by the code it guards and the test would pass having proved
    # nothing — while the developer's real login Keychain got a new item.
    from scribejay.core import secrets

    with pytest.raises(BaseException) as excinfo:
        secrets.set("SCRIBEJAY_CONFTEST_PROBE", "probe")
    assert not isinstance(excinfo.value, Exception), \
        "the Keychain guard is a plain Exception — core/secrets.py will swallow it"


def test_keychain_reads_still_degrade_quietly():
    # Reads must NOT raise: resolve_key() consults the Keychain on every
    # credential miss, and that path is exercised all over the suite. A guard
    # that raised here would turn a normal miss into a failure.
    from scribejay.core import secrets

    assert secrets.get("SCRIBEJAY_CONFTEST_PROBE") is None
    assert secrets.is_set("SCRIBEJAY_CONFTEST_PROBE") is False


# --------------------------------------------------------------------------- #
# Features — the gate every task now runs through
# --------------------------------------------------------------------------- #

def test_every_feature_is_explicitly_enabled_for_the_suite():
    # Without this, features.enabled() falls through to a probe of the machine
    # — which under the redirects above answers "no" for everything, so every
    # task's registry guard fires and hundreds of tests silently exercise a
    # four-line skip instead of the task they name.
    from scribejay.core import features

    off = [n for n in features.NAMES if not features.enabled(n)]
    assert not off, f"features not enabled for the suite: {off}"


def test_the_feature_probe_is_not_what_the_suite_relies_on(monkeypatch):
    # Explicitly ON, not "on because the developer's machine has it". The probe
    # reads real paths (Chrome's history file, the Google client JSON), and
    # depending on it would put the developer's machine back in the loop —
    # exactly the coupling the settings redirects removed.
    from scribejay.core import features

    monkeypatch.setattr(features, "configured", lambda n: False)
    assert all(features.enabled(n) for n in features.NAMES)
