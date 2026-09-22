"""Compose and write a daily "AI Chat Learnings" review to the user's Obsidian vault
(one file per day). Non-interactive — run by launchd every morning, covering the
prior day's AI-agent chats.

For each Claude Code or top-level Codex Desktop session that was new or revisited
yesterday (and each new file in the Gemini drop folder), the local model writes
a brief Accomplished / Learned summary — not the back-and-forth, just what got
done and what was learned. Python owns the day math, section headers, and file
assembly; the model only writes the bullets. A day with no chats writes nothing
(keeps the vault clean).

The sources are what lands on disk: Claude Code and Codex Desktop's local session
logs, and a folder the user drops Gemini exports into (see
scribejay/sources/transcripts.py).

Usage:
    python -m scribejay.ai_chat_learnings                 # yesterday (all sources)
    python -m scribejay.ai_chat_learnings --backfill 14   # each of the last 14 days
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scribejay.core import config, registry
from scribejay.core.dates import local_timezone, prior_day
from scribejay.core.logs import notify_failure, setup_logger
from scribejay.core.model import backend as scribejay_backend, complete_text, log_backend, warm_model
from scribejay.core.store import atomic_write_json, load_json, locked
from scribejay.core.text import safe_label
from scribejay.sinks.vault import persist_or_email
from scribejay.sources.transcripts import (
    DEFAULT_MAX_CHARS,
    fetch_claude_sessions,
    fetch_codex_sessions,
    fetch_gemini_chats,
    gemini_dir,
)

# Gemini dedup: the watermark that stops a re-run re-summarizing a drop file.
_STATE_FILENAME = "ai_chat_learnings_state.json"
_LEGACY_STATE_PATH = Path(__file__).resolve().parent.parent / "config" / _STATE_FILENAME


def _resolve_state_path(legacy: Path) -> Path:
    """Where the dedup store lives, on the same rule as config.resolve_path().

    A pre-packaging checkout has the file beside the repo and keeps using it —
    moving the watermark would re-summarize everything it had already recorded.
    Anything else gets ~/.scribejay, because installed as a tool the source
    tree is site-packages: not a place anything may write, and replaced whole
    by the next reinstall. A store lost that way fails silently, since a
    duplicate summary looks exactly like a new one."""
    return legacy if legacy.exists() else config.config_dir() / _STATE_FILENAME


STATE_PATH = _resolve_state_path(_LEGACY_STATE_PATH)

SESSION_SYSTEM_PROMPT = f"""You are {config.user_name()}'s assistant. You are given ONE past chat \
session {config.user_name()} had with an AI agent. Summarize it into a brief, skimmable log entry — \
focus ONLY on what was accomplished and what was learned, NOT the back-and-forth or the reasoning \
behind each decision. You are running unattended — infer everything from the transcript given.

Use EXACTLY this template (no extra sections, do not include the literal brackets):

**Accomplished**
- [a concrete outcome, deliverable, or decision from the session]
**Learned**
- [a durable insight, gotcha, or fact worth remembering later]

Rules:
- 1-4 Accomplished bullets, 0-3 Learned bullets. Be terse — one line each.
- If nothing was genuinely accomplished, write a single "- None" under Accomplished.
- If there was no real learning, write a single "- None" under Learned.
- Base everything ONLY on the transcript. Do not invent work that isn't there.
- Never repeat a word or phrase; no preamble.

Output ONLY the filled-in template, nothing else.
"""


def _has_real_content(text: str) -> bool:
    """True if the summary has at least one bullet that isn't the "- None"
    empty-section marker — lets us skip a session the model found nothing in."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and stripped[2:].strip().lower() != "none":
            return True
    return False


def _session_header(source: str, session: dict) -> str:
    """The `### ` line for one chat. Every borrowed field is neutralized.

    `slug` is the one that needs it: it comes out of a Claude Code session
    JSONL, written by a model from the conversation's own content, and
    AGENTS.md puts model output that reaches a written file on the untrusted
    side of the line. `project` is a directory name off this machine and
    `source` is a literal, so neither is a stranger's word — they go through
    anyway, because a rule that holds for two of three fields is a rule nobody
    can check.

    A newline is the live failure, not a forged link: one `\n` in any of these
    splits this header and turns the model's summary below it into a section of
    its own. safe_label collapses it. `or "unknown"` covers a `project` made
    only of syntax, because a header of "Claude ·  · 9:14 AM" reads as a bug."""
    parts = [source, safe_label(session["project"]) or "unknown"]
    if session["slug"]:
        parts.append(safe_label(session["slug"]))
    parts.append(f"{session['started_at']:%-I:%M %p}")
    return " · ".join(parts)


def _summarize(text: str, source: str, logger, backend) -> str:
    user_prompt = f"source: {source}\n\ntranscript:\n{text}\n"
    summary = complete_text(
        system_prompt=SESSION_SYSTEM_PROMPT, user_prompt=user_prompt,
        logger=logger, backend=backend, think=False,
    )
    logger.info(f"Summarized a {source} chat:\n{summary}")
    return summary


def _prune_processed(processed: dict) -> dict:
    """Drop the rows whose file has left the drop folder.

    AGENTS.md asks every store to prune on write, and this one only ever grew:
    one row per Gemini export, forever, on a store polled every day.

    Age is the wrong rule for it, though. The row IS the watermark. A chat
    dropped a year ago and never cleared out is still sitting in the folder,
    and dropping its row would re-summarize that chat into tomorrow's page —
    the exact duplicate the store exists to prevent. What makes a row dead is
    the file being gone: once it is, no later run can ever match on that name
    again, so the row can never do anything but take up space.

    A missing or unreadable folder prunes nothing. The feature stays idle until
    someone points the setting at a real directory, and reading "no folder" as
    "no files" would empty the whole store on the first run of a machine whose
    drop folder is not set up yet.
    """
    directory = gemini_dir()
    try:
        present = {path.name for path in directory.iterdir()}
    except OSError:
        return processed
    return {name: mtime for name, mtime in processed.items() if name in present}


def _mark_processed(newly_processed: dict) -> None:
    """Write the Gemini watermark. Call this only once the day is safely out.

    The watermark is what stops a re-run re-summarizing a drop file, so writing
    it before the page is persisted turns any persist failure into permanent
    loss: `persist_or_email` raises when both the vault write AND the email
    fallback fail, and these chats are marked done by then, so no later run
    will ever look at them again.

    The single-sink failure is the likelier one and loses something too. If the
    vault write fails but the email gets through, the content reached the user
    — but the page was never written, and a watermark set here would stop a
    later run from rebuilding it. Fixing the folder and re-running would then
    produce a page with the Claude and Codex sections and a silent hole where
    the Gemini ones were.
    """
    if not newly_processed:
        return
    with locked(STATE_PATH):
        state = load_json(STATE_PATH, {"gemini_processed": {}})
        merged = {**state.get("gemini_processed", {}), **newly_processed}
        state["gemini_processed"] = _prune_processed(merged)
        atomic_write_json(STATE_PATH, state)


def _run_for_day(start, end, day, include_gemini, backend, max_chars, logger) -> None:
    """Build and persist one day's file. include_gemini is False for backfill
    runs (the drop folder has no reliable per-day dates, so it's only folded into
    the normal "yesterday" run)."""
    claude = fetch_claude_sessions(start, end, max_chars)
    codex = fetch_codex_sessions(start, end, max_chars, logger=logger)
    logger.info(f"{len(claude)} Claude session(s) active on {day}")
    logger.info(f"{len(codex)} Codex session(s) active on {day}")
    sessions = sorted(
        [("Claude", session) for session in claude]
        + [("Codex", session) for session in codex],
        key=lambda item: item[1]["started_at"],
    )

    gemini, processed = [], {}
    if include_gemini:
        processed = load_json(STATE_PATH, {"gemini_processed": {}}).get("gemini_processed", {})
        gemini = fetch_gemini_chats(processed, max_chars)
        logger.info(f"{len(gemini)} new Gemini chat(s) in the drop folder")

    if not sessions and not gemini:
        logger.info(f"No chats for {day}; nothing to write")
        return

    sections = []
    for source, session in sessions:
        summary = _summarize(session["text"], source, logger, backend)
        if _has_real_content(summary):
            sections.append(f"### {_session_header(source, session)}\n{summary}")

    newly_processed = {}
    for chat in gemini:
        summary = _summarize(chat["text"], "Gemini", logger, backend)
        # Mark processed whether or not it was useful, so we never re-summarize it.
        newly_processed[chat["name"]] = chat["mtime"]
        if _has_real_content(summary):
            # The filename is the user's own, but it reaches a header the same
            # way a slug does, and _session_header's reasoning applies here too.
            name = safe_label(Path(chat["name"]).stem) or "unknown"
            sections.append(f"### Gemini · {name}\n{summary}")

    if not sections:
        logger.info(f"No substantive chat summaries for {day}; nothing to write")
        # Nothing to persist, so nothing can fail after this — and a chat the
        # model found nothing in is still done with. Marking it here is what
        # stops it being re-summarized every morning forever.
        _mark_processed(newly_processed)
        return

    body = f"## AI Chat Learnings: {day:%B %-d, %Y}\n\n" + "\n\n".join(sections) + "\n"
    persist_or_email(
        body, "AI-Chat-Learnings", day,
        subject=f"AI Chat Learnings (needs manual paste) - {day:%Y-%m-%d}",
        task_name="ai_chat_learnings", logger=logger,
    )
    # After, never before. persist_or_email raises when it cannot deliver at
    # all, and letting that skip the watermark is the whole point: those chats
    # stay unprocessed and tomorrow's run picks them up again.
    _mark_processed(newly_processed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="summarize a single day YYYY-MM-DD (Claude + Codex) — "
                             "one process per day is the gentle way to backfill history")
    parser.add_argument("--backfill", type=int, default=0,
                        help="summarize each of the last N days as a separate run "
                             "(Claude + Codex); default 0 = just yesterday")
    args = parser.parse_args()

    logger = setup_logger("ai_chat_learnings")
    logger.info("Starting ai chat learnings run")

    if registry.skip_if_disabled("ai_chat_learnings", logger):
        return 0

    try:
        max_chars = int(config.getenv("AI_CHAT_LEARNINGS_MAX_CHARS", DEFAULT_MAX_CHARS))
        backend = scribejay_backend("ai_chat_learnings")
        log_backend(logger, "ai_chat_learnings", backend)
        warm_model(logger=logger, backend=backend)

        if args.date:
            tz = ZoneInfo(local_timezone())
            start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=tz)
            end = start.replace(hour=23, minute=59, second=59)
            day = start.date()
            logger.info(f"Single day: {day}")
            _run_for_day(start, end, day, include_gemini=False,
                         backend=backend, max_chars=max_chars, logger=logger)
        elif args.backfill > 0:
            tz = ZoneInfo(local_timezone())
            today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            # Oldest day first, so the log reads chronologically as files land.
            for k in range(args.backfill, 0, -1):
                start, end, day = prior_day(today - timedelta(days=k - 1))
                logger.info(f"Backfill day {day}")
                _run_for_day(start, end, day, include_gemini=False,
                             backend=backend, max_chars=max_chars, logger=logger)
        else:
            start, end, day = prior_day()
            logger.info(f"Day: {day}")
            _run_for_day(start, end, day, include_gemini=True,
                         backend=backend, max_chars=max_chars, logger=logger)

        logger.info("AI chat learnings run complete")
        return 0
    except Exception as e:
        logger.exception(f"AI chat learnings run failed: {e}")
        notify_failure("ai_chat_learnings", e, logger)
        return 1


if __name__ == "__main__":
    sys.exit(main())
