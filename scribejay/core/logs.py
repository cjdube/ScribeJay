"""Shared helpers for ScribeJay's task runners (unattended entrypoints run by launchd).

There is no recovery coordinator in this repo, so a failed run always pushes
its own alert immediately rather than deferring to one.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from scribejay.core import config
from scribejay.core.notify import notify

# ~/.scribejay/logs by default, not logs/ beside the source. Installed with
# `uv tool install` there is no source tree to write beside — it is
# site-packages. The directory moved in Phase 5; the FILE NAMES did not, and
# must not: docs/architecture.md is explicit that run history is keyed off the
# basenames.
LOGS_DIR = config.resolve_path(config.getenv("SCRIBEJAY_LOGS_DIR"))

# <task>.log rotates below, but <task>.launchd.log is written by launchd, not
# by us, and nothing rotated it — eight jobs appending every morning for the
# life of an install. Trimmed here instead, once per run, to roughly the same
# bound the rotating handler keeps.
_LAUNCHD_LOG_MAX_BYTES = 1_000_000
_LAUNCHD_LOG_KEEP_BYTES = 200_000


def _trim_launchd_log(path: Path) -> None:
    """Cut launchd's own stdout file back to its tail when it gets too big.

    **Truncated in place, never renamed.** launchd opens StandardOutPath by
    path for each run and appends; renaming it would leave this run writing
    into an inode with no name, and the next run creating a fresh file nobody
    connects to the last. Truncation is safe against the same open descriptor
    because O_APPEND seeks to the end at write time.

    The tail is kept rather than the file emptied. What lands in this file is
    what launchd said *before* setup_logger existed — a failed exec, an import
    that died — so it is the only record of the failures <task>.log cannot
    hold. Emptying it would throw away the thing it is for.

    Any OSError is swallowed: housekeeping on a log must never be the reason a
    run does not happen.
    """
    try:
        if not path.exists() or path.stat().st_size <= _LAUNCHD_LOG_MAX_BYTES:
            return
        with open(path, "rb") as f:
            f.seek(-_LAUNCHD_LOG_KEEP_BYTES, 2)
            tail = f.read()
        # Drop the first line: seeking by bytes lands mid-line, and half a
        # timestamp reads as corruption to anyone tailing this.
        _, _, tail = tail.partition(b"\n")
        with open(path, "wb") as f:
            f.write(b"[earlier output trimmed by ScribeJay]\n" + tail)
    except OSError:
        pass


def setup_logger(task_name: str) -> logging.Logger:
    # parents=True: the default now sits two levels down (~/.scribejay/logs),
    # and the first task to run may be the first thing to need either level.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{task_name}.log"
    _trim_launchd_log(LOGS_DIR / f"{task_name}.launchd.log")

    logger = logging.getLogger(task_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Rotate so a long-lived log can't grow without bound.
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    # Settings problems are found at import, before any logger exists, so
    # scribejay/core/config.py parks them here instead of logging into the
    # void. Replayed once, into the first task logger built: a settings file
    # that is being silently ignored has to reach the run log, which is where
    # a dashboard and a human both look.
    while config.STARTUP_WARNINGS:
        logger.warning(f"config: {config.STARTUP_WARNINGS.pop(0)}")

    return logger


def notify_failure(task_name: str, detail: object, logger: logging.Logger = None) -> None:
    """Push a one-line failure alert for a scheduled task (best-effort).

    Swallows any error from the push itself so an ntfy outage can never mask
    the original task failure — the failure is already logged by the caller.

    Falls back to email if the push doesn't land: this alert fires once and is
    gone if it doesn't arrive, and nothing retries it."""
    try:
        result = notify(
            message=f"{task_name} failed: {detail}",
            title=f"ScribeJay: {task_name} failed",
            priority="high",
            email_fallback=True,
        )
        if logger and result.get("error"):
            logger.warning(f"Failure push via ntfy did not send: {result['error']}")
    except Exception:
        if logger:
            logger.exception("notify_failure raised while sending the failure push")
