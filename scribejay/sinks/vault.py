"""Write a learnings review to a Markdown file in the user's Obsidian vault,
falling back to email if the write fails.

Mirrors LocalLLMAgent's agent/tools/learnings_file.py (write_entry) plus the
persist_or_email half of agent/activity_log.py — grouped together here because
both are sink operations: where a finished draft ends up.
"""

from pathlib import Path

from scribejay.core import config
from scribejay.core.logs import notify_failure
from scribejay.sinks.email import send_email

DEFAULT_LEARNINGS_DIR = str(Path.home() / "Documents" / "ScribeJay")


def _learnings_dir() -> Path:
    return Path(config.getenv("LEARNINGS_DIR", DEFAULT_LEARNINGS_DIR)).expanduser()


def write_entry(content: str, prefix: str, day, directory: str | Path | None = None) -> dict:
    """Write `content` to <prefix>-<day:%Y-%m-%d>.md in `directory`, defaulting to
    the learnings dir. Does NOT create parent dirs: a missing dir means the
    configured path is wrong or the vault moved, and mkdir-ing it would file
    reviews into a stray tree nobody reads. Return an error instead and let the
    caller email the draft."""
    directory = Path(directory).expanduser() if directory else _learnings_dir()
    if not directory.exists():
        return {"error": f"target dir not found (check the caller's configured path): {directory}"}
    try:
        path = directory / f"{prefix}-{day:%Y-%m-%d}.md"
        path.write_text(content)
    except Exception as e:
        return {"error": str(e)}
    return {"written": True, "path": str(path)}


def persist_or_email(content: str, prefix: str, day, subject: str,
                     task_name: str, logger, directory=None) -> dict:
    """Write `content` to the vault as <prefix>-<day>.md; if the write fails
    (e.g. the vault dir is missing), email the draft instead so it's never lost.
    Both paths failing is a hard failure (alert + raise). `directory` overrides
    the default learnings-dir target for callers writing outside the vault's
    ingest queue."""
    write_result = write_entry(content, prefix, day, directory=directory)
    logger.info(f"write_entry -> {write_result}")
    if "error" in write_result:
        logger.warning("File write failed — emailing the draft so it isn't lost")
        notify_failure(task_name, "vault write failed — draft emailed instead", logger)
        email_result = send_email(subject=subject, body=content)
        logger.info(f"send_email -> {email_result}")
        if "error" in email_result:
            # send_email returns error dicts rather than raising, so check it:
            # both persistence paths failing must surface as a failed run.
            raise RuntimeError(
                "vault write AND email fallback both failed: "
                f"{email_result['error']}"
            )
    return write_result
