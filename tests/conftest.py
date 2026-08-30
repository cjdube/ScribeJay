"""Shared pytest fixtures.

Mirrors LocalLLMAgent's tests/conftest.py, scaled down to what ScribeJay
actually has. ScribeJay owns no dashboard, no opportunities/memory/bg_jobs/
reminders/mail_state/clickup_watcher/wiki/starred/escalations/games stores,
and no daemon threads — every fixture in the source conftest that exists to
guard one of those is simply not needed here.
What ScribeJay DOES have, and must protect the same way:

- Every task calls `setup_logger`, which writes to the real `logs/` dir if
  left alone. Redirected below, with the same hard-block backstop as Wren's:
  a missed redirect fails loudly (in the test that caused it) instead of
  quietly appending fixture rows into the user's real logs.
- Every learnings/correspondence task writes into the user's real Obsidian
  vault (LEARNINGS_DIR, CORRESPONDENCE_DIR) unless redirected.
- `notify_failure` POSTs to the real ntfy server and falls back to a real
  Gmail send on a failure path every task's tests exercise on purpose.
  Both egress channels are stubbed suite-wide.
- `ai_chat_learnings` has its own dedup store (STATE_PATH) and reads real
  `~/.claude` / `~/.codex` / Gemini-drop-folder paths off disk.
- ClickUp and Gemini are both live network egress the suite must never reach.
- `daily_commits` reads the machine: `sources/git.py` walks PROJECTS_DIR
  (defaulting to the developer's real ~/Projects) and shells out to git for
  every checkout there — including `fetch_repos()`, which runs `git fetch
  --all`. That is network egress AND a write into real repositories, so it
  gets the same pin Wren gives its own project scanner.
"""

import logging
import os
import tempfile
from pathlib import Path

import pytest

from scribejay.core import logs as _logs
from scribejay.core import notify as _notify
from scribejay.core.backends import gemini as _gemini_backend
from scribejay.sinks import email as _email
from scribejay.sources import clickup as _clickup
from scribejay.sources import transcripts as _chat_transcripts
from scribejay import ai_chat_learnings as _ai_chat_learnings

# Resolved from the source tree rather than from any redirect, so it still names
# the real directory when a redirect is the thing that's broken.
_REAL_LOGS_DIR = Path(_logs.__file__).resolve().parent.parent.parent / "logs"

# Both lines run at conftest import — before any test module runs. The env var
# covers a child interpreter, should one ever be spawned; the attribute covers
# this process.
_TEST_LOGS_DIR = Path(tempfile.mkdtemp(prefix="scribejay-test-logs-"))
os.environ["SCRIBEJAY_LOGS_DIR"] = str(_TEST_LOGS_DIR)
_logs.LOGS_DIR = _TEST_LOGS_DIR


def _forbid_production_log_handlers() -> None:
    """Make a log handler on the real logs/ raise instead of quietly appending.

    The backstop behind the redirect above: that moves the path, this refuses
    the write. Patched onto FileHandler, which RotatingFileHandler — what
    setup_logger actually builds — constructs through. Installed at import,
    permanently and process-wide, so a missed redirect fails in the test that
    caused it rather than being discovered in the log later.
    """
    original_init = logging.FileHandler.__init__

    def _guarded_init(self, filename, *args, **kwargs):
        if Path(filename).resolve().parent == _REAL_LOGS_DIR:
            raise RuntimeError(
                f"a test tried to open the production log {filename} — the logs/ "
                "redirect in tests/conftest.py is not in effect for whatever built "
                "this handler."
            )
        return original_init(self, filename, *args, **kwargs)

    logging.FileHandler.__init__ = _guarded_init


_forbid_production_log_handlers()


@pytest.fixture(autouse=True)
def _isolate_task_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(_logs, "LOGS_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _isolate_vault_dirs(tmp_path, monkeypatch):
    # scribejay.sinks.vault._learnings_dir() reads this env at call time.
    monkeypatch.setenv("LEARNINGS_DIR", str(tmp_path))
    # scribejay.correspondence._correspondence_dir() reads this env at call
    # time — a second real vault path, deliberately kept out of the ingest
    # queue, and it gets the same backstop.
    monkeypatch.setenv("CORRESPONDENCE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolate_ai_chat_learnings(tmp_path, monkeypatch):
    # Redirect the Gemini-dedup store to tmp, and point all chat sources away
    # from the user's real data: no test may read ~/.claude or ~/.codex session
    # transcripts or the real Gemini drop folder, and none may write the
    # production state store.
    monkeypatch.setattr(_ai_chat_learnings, "STATE_PATH",
                        tmp_path / "ai_chat_learnings_state.json")
    monkeypatch.setattr(_chat_transcripts, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr(_chat_transcripts, "CODEX_SESSIONS_DIR", tmp_path / "codex_sessions")
    monkeypatch.setenv("SCRIBEJAY_GEMINI_CHATS_DIR", str(tmp_path / "gemini_inbox"))


class _StubNtfyResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"healthy": True}


@pytest.fixture(autouse=True)
def _block_ntfy_egress(monkeypatch):
    """Stub both verbs the ntfy module speaks — post (publish) and get (health).
    notify_failure fires a real push at the user's phone on every task-failure
    path a test exercises; test_notify.py re-patches both per-test to exercise
    the real code."""
    monkeypatch.setattr(_notify.requests, "post", lambda *a, **k: _StubNtfyResponse())
    monkeypatch.setattr(_notify.requests, "get", lambda *a, **k: _StubNtfyResponse())


@pytest.fixture(autouse=True)
def _block_email_send(monkeypatch):
    # persist_or_email's fallback and calendar_colorizer's failure notice both
    # send a real Gmail message when left unstubbed, which put an egress path
    # behind failure branches tests exercise on purpose.
    def _no_real_email(*a, **k):
        raise RuntimeError("real Gmail send blocked in tests — stub send_email")
    monkeypatch.setattr(_email, "send_email", _no_real_email)


class _ClickUpEgress(BaseException):
    """Deliberately NOT an Exception. closed_tasks() ends in
    `except Exception: return http_error(e)` (degrade-don't-crash), so an
    ordinary error raised by the guard below would be caught by the very code
    it is guarding and returned as a tidy {"error": ...} — the test would pass
    having proved nothing, and a real run would still have gone to the network.
    Inheriting from BaseException is what makes the guard reach the test
    runner."""


@pytest.fixture(autouse=True)
def _block_clickup_egress(monkeypatch):
    """Every ClickUp call reaches api.clickup.com with the user's REAL personal
    token, because _client() loads config/.env at call time. Loud on purpose —
    the message names the fix. test_clickup.py re-patches this per test."""
    def _blocked(*a, **k):
        raise _ClickUpEgress(
            "a test reached the live ClickUp API. Stub the caller's clickup "
            "function or clickup._get."
        )
    monkeypatch.setattr(_clickup, "_get", _blocked)


@pytest.fixture(autouse=True)
def _isolate_projects_dir(tmp_path, monkeypatch):
    """Pin the commit scanner at an empty tmp dir.

    `scribejay/sources/git.py` *reads the machine*: `_projects_dir()` defaults
    to the developer's real ~/Projects, and `daily_commits` walks every
    checkout under it, shelling out to git for each one. So an unpinned test
    would depend on which repos they happen to have cloned, and would spend a
    few dozen subprocesses per test finding out.

    Unlike Wren's equivalent this is not only about determinism. `fetch_repos()`
    runs `git fetch --all` on every checkout it finds — real network egress to
    every configured remote, and a write into the .git directory of the user's
    actual repositories (this checkout among them). That is the one path in
    ScribeJay that mutates something outside its own tree.

    `_projects_dir()` reads the env on every call, so the env var is enough;
    test_git.py points it at its own fixture tree per-test. This is the
    backstop that makes missing one harmless."""
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "projects_dir"))


@pytest.fixture(autouse=True)
def _block_gemini_client(monkeypatch):
    def _no_real_gemini(*a, **k):
        raise RuntimeError(
            "real Gemini client blocked in tests — stub "
            "scribejay.core.backends.gemini._gemini_client")
    monkeypatch.setattr(_gemini_backend, "_gemini_client", _no_real_gemini)
