"""Compose and write a daily YouTube-learnings review to the user's Obsidian vault
(one file per day). Non-interactive — run by launchd every morning, covering the
prior day's Liked videos.

The user deliberately Likes AI/tooling, product-management and thinking/reasoning videos, so the day's
Likes are a clean signal for what he chose to learn. The model writes a short synthesis of what the videos
teach; a deterministic, scheme-validated linked list of the exact videos is
appended in Python. A day with no Likes writes nothing (keeps the vault clean).

The scheduled run catches up: it covers every day since the last one it
finished, up to MAX_CATCHUP_DAYS, so a week with the Mac off still gets its
pages. The last finished day is kept in `~/.scribejay` (see state_path), not
read off the vault — a day with no Likes leaves no file, so a missing file
cannot tell a quiet day from a missed one. --date and --dry-run never move it.

Usage:
    python -m scribejay.daily_youtube_learnings
    python -m scribejay.daily_youtube_learnings --date 2026-09-03
    python -m scribejay.daily_youtube_learnings --date 2026-09-03 --dry-run
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scribejay.core import config, registry
from scribejay.core.dates import local_timezone, prior_day, resolve_date
from scribejay.core.logs import notify_failure, setup_logger
from scribejay.core.model import backend as scribejay_backend, complete_text, log_backend, warm_model
from scribejay.core.store import atomic_write_json, load_json, locked
from scribejay.journal import videos_section
from scribejay.sinks.vault import persist_or_email
from scribejay.sources.youtube import fetch_liked_videos

# How far back one scheduled run reaches. Two weeks covers a holiday with the
# Mac off; a longer gap is the --date flag's job, one day at a time.
MAX_CATCHUP_DAYS = 14

STORE_NAME = "youtube_learnings_state.json"

DRAFT_SYSTEM_PROMPT = f"""You are {config.user_name()}'s personal executive assistant. You write a \
short thematic synthesis of the AI/technical, product-management, and thinking-and-reasoning \
(critical thinking, judgment, cognitive bias, decision-making, mental models, learning) YouTube \
videos he Liked yesterday — the videos he chose to learn from. You are running unattended — infer everything from the data given.

Use EXACTLY this template, filling in the bracketed parts (do not add extra sections, do not
include the literal brackets):

## YouTube Learnings: [Month Date, Year]

### Themes Explored
- **[Theme/Tool]:** [What this cluster of videos teaches and how it could shape future work]

Rules:
- Write **2-4 bullets total** that GROUP the videos into themes — NOT one bullet per video. \
(Every video is already listed verbatim below your synthesis, so do not re-list them.)
- Bold the theme/tool at the start of each bullet.
- Professional, analytical tone. Explain what the cluster teaches and why it matters.
- Keep it tight — a few sentences per bullet. Never repeat a word or phrase.

Output ONLY the filled-in template text, nothing else — no preamble, no explanation.
"""


def _looks_usable(text: str) -> bool:
    """The small model occasionally returns nothing, or a degenerate run that
    strip() collapses to whitespace. Treat a synthesis as usable only if it's
    non-empty and has at least one bullet."""
    return bool(text) and "- " in text


def _window(date_str: str | None):
    """(start, end, day) for --date, or yesterday when it is not given."""
    if not date_str:
        return prior_day()
    tz = ZoneInfo(local_timezone())
    day = datetime.fromisoformat(resolve_date(date_str)).date()
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=tz)
    return start, start.replace(hour=23, minute=59, second=59), day


def _fetch(day, logger) -> dict:
    """fetch_liked_videos for one day, logged. Keeps the "error" key."""
    start, end, _ = _window(f"{day:%Y-%m-%d}")
    result = fetch_liked_videos(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    logger.info(f"fetch_liked_videos -> {result}")
    return result


def gather(day, logger) -> list:
    """That day's Liked videos. Empty when nothing was Liked, and empty on a
    fetch that degraded — the caller cannot tell the two apart. run_day does
    not use this: a catch-up must not mark a failed day as done."""
    return _fetch(day, logger).get("videos", [])


def state_path():
    """Under `~/.scribejay` via resolve_path, never beside the source tree.
    A function rather than a constant so a test's redirect is seen."""
    return config.resolve_path(STORE_NAME)


def _last_day() -> date | None:
    """The last day the scheduled run finished, or None before its first."""
    try:
        return date.fromisoformat(load_json(state_path(), {}).get("last_day", ""))
    except (AttributeError, TypeError, ValueError):
        return None


def _save_last_day(day) -> None:
    """Move the marker forward to `day`. Never backward."""
    with locked(state_path()):
        data = load_json(state_path(), {})
        if not isinstance(data, dict):
            data = {}
        done = data.get("last_day")
        if not isinstance(done, str) or done < f"{day:%Y-%m-%d}":
            atomic_write_json(state_path(), {**data, "last_day": f"{day:%Y-%m-%d}"})


def days_to_cover(yesterday, last_day) -> list:
    """Each day after `last_day` through `yesterday`, oldest first, at most
    MAX_CATCHUP_DAYS. Yesterday alone before the first run, so the first run
    does not suddenly write two weeks of pages."""
    if last_day is None:
        return [yesterday]
    first = max(last_day + timedelta(days=1),
                yesterday - timedelta(days=MAX_CATCHUP_DAYS - 1))
    return [first + timedelta(days=k) for k in range((yesterday - first).days + 1)]


def build_prompt(day, videos: list) -> str:
    """The synthesis prompt for one day.

    Titles and channels only: the Liked-video descriptions are long
    clickbait/link-dumps that derail the small model into repetition loops, and
    the titles here already say what each video is about.
    """
    titles = [{"title": v.get("title"), "channel": v.get("channel")} for v in videos]
    return f"day: {day:%B %-d, %Y}\nyoutube_videos: {titles}\n"


def run_day(day, logger, backend: str | None, dry_run: bool, warm) -> bool:
    """Write one day. False when the fetch failed, so the day is not done.
    `warm` loads the model, once per run however many days it covers."""
    result = _fetch(day, logger)
    if "error" in result:
        logger.warning(f"YouTube fetch failed for {day}; it is retried next run")
        return False
    videos = result.get("videos", [])
    if not videos:
        # Nothing Liked that day — don't litter the vault with an empty file.
        logger.info(f"No videos Liked on {day}; nothing to write")
        return True

    warm()
    synthesis = complete_text(
        system_prompt=DRAFT_SYSTEM_PROMPT, user_prompt=build_prompt(day, videos),
        logger=logger, backend=backend, think=False,
    )
    logger.info(f"Drafted synthesis:\n{synthesis}")

    # Degrade, don't emit garbage: if the small model returned nothing usable
    # (empty, or a degenerate run collapsed to whitespace by strip()), write
    # the verbatim list under a plain header rather than a broken synthesis.
    if _looks_usable(synthesis):
        body = f"{synthesis}\n\n{videos_section(videos)}\n"
    else:
        logger.warning("synthesis unusable; writing the video list without it")
        body = f"## YouTube Learnings: {day:%B %-d, %Y}\n\n{videos_section(videos)}\n"

    if dry_run:
        logger.info("Dry run — not writing or emailing")
        print(body)
        return True

    persist_or_email(
        body, "Daily-YouTube", day,
        subject=f"YouTube Learnings (needs manual paste) - {day:%Y-%m-%d}",
        task_name="daily_youtube_learnings", logger=logger,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="write a single day YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="draft and print, but write and email nothing")
    args = parser.parse_args()

    dry_run = args.dry_run

    # A dry run gets its own log file, for the same reason the chrome task's
    # does: cli/doctor.py:last_run reads logs/daily_youtube_learnings.log for
    # "Starting"/"run complete" to decide whether the 5:05 job is healthy, and
    # a sibling repo reads the same folder — so a hand-run experiment writing
    # those lines would report a scheduled run that never happened.
    logger = setup_logger("daily_youtube_learnings_dryrun" if dry_run
                          else "daily_youtube_learnings")
    logger.info("Starting daily youtube learnings run")

    if registry.skip_if_disabled("daily_youtube_learnings", logger):
        return 0

    try:
        backend = scribejay_backend("daily_youtube_learnings")
        log_backend(logger, "daily_youtube_learnings", backend)

        warmed = []

        def warm():
            if not warmed:
                warm_model(logger=logger, backend=backend)
                warmed.append(True)

        if args.date:
            _, _, day = _window(args.date)
            logger.info(f"Day: {day}")
            run_day(day, logger, backend, dry_run, warm)
        else:
            _, _, yesterday = prior_day()
            days = days_to_cover(yesterday, _last_day())
            if not days:
                logger.info(f"Already covered through {yesterday}; nothing to do")
            for day in days:
                logger.info(f"Catch-up day {day}")
                if not run_day(day, logger, backend, dry_run, warm):
                    break
                # A dry run previews the catch-up but must not move the
                # scheduled job's marker.
                if not dry_run:
                    _save_last_day(day)

        logger.info("Daily youtube learnings run complete")
        return 0
    except Exception as e:
        logger.exception(f"Daily youtube learnings run failed: {e}")
        notify_failure("daily_youtube_learnings", e, logger)
        return 1


if __name__ == "__main__":
    sys.exit(main())
