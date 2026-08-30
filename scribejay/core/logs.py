"""Shared helpers for ScribeJay's task runners (unattended entrypoints run by launchd).

Mirrors LocalLLMAgent's tasks/_common.py, minus the startup_recovery detour —
Wren's recovery coordinator does not exist in this repo, so a failed run
always pushes its own alert immediately.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from scribejay.core import config
from scribejay.core.notify import notify

# ~/.scribejay/logs by default, not logs/ beside the source. Installed with
# `uv tool install` there is no source tree to write beside — it is
# site-packages. The directory moved in Phase 5; the FILE NAMES did not, and
# must not: docs/architecture.md is explicit that run history is keyed off the
# basenames.
LOGS_DIR = config.resolve_path(config.getenv("SCRIBEJAY_LOGS_DIR"))


def setup_logger(task_name: str) -> logging.Logger:
    # parents=True: the default now sits two levels down (~/.scribejay/logs),
    # and the first task to run may be the first thing to need either level.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{task_name}.log"

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
