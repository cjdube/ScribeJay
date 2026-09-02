"""Write a daily record of who he talked to, into CORRESPONDENCE_DIR.

Non-interactive — run by launchd every morning, covering the prior day. Reads
both halves of the day as metadata (headers only, never bodies) via
`scribejay.sources.gmail`: `fetch_sent_metadata` for what he wrote and
`fetch_inbox_metadata` for what arrived. Drops mail sent to himself, groups the
rest by conversation, adds the threads that have been open too long, and writes
the page in Python.

**A failing half is not an empty half.** Each fetcher degrades to
{"error": ...}, so a day Gmail refused to answer and a day he wrote to nobody
arrive here looking identical. `_messages` keeps them apart — None for the
first, [] for the second — because only one of them should cost the page.

**No model call.** With no bodies there is nothing to summarize that the subject
line does not already say, and a richer sentence would be invented — see
scribejay/correspondence.py. `scribejay/strava_download.py` is the other task
shaped this way. It is also what keeps a stranger's subject line away from a
prompt: reading the inbox adds untrusted text, and there is nothing here for it
to steer.

**Not the vault's raw/.** These pages name people and the companies they work
for; the ingest queue would turn them into asserted wiki pages. See
`_correspondence_dir`.

Usage:
    python -m scribejay.daily_correspondence                 # yesterday
    python -m scribejay.daily_correspondence --date 2026-08-21
    python -m scribejay.daily_correspondence --backfill 14   # each of the last 14 days
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scribejay.core import registry
from scribejay.core.dates import local_timezone, prior_day
from scribejay.core.logs import notify_failure, setup_logger
from scribejay.correspondence import (
    _correspondence_dir,
    filter_inbound_noise,
    filter_noise,
    group_day,
    load_threads,
    quiet_threads,
    remember_threads,
    render_page,
)
from scribejay.sinks.vault import persist_or_email
from scribejay.sources.gmail import (
    fetch_inbox_metadata,
    fetch_sent_metadata,
    my_address,
)


def _messages(name: str, result: dict, day, logger):
    """One fetcher's rows, or None when it failed.

    None rather than [] on purpose: a day he wrote to nobody and a day Gmail
    would not answer look identical downstream, and only one of them should
    cost the page. The WARNING is what says which — a source that fails reads
    as empty to its caller, but never silently."""
    if "error" in result:
        logger.warning(f"{name} failed for {day}: {result['error']}")
        return None
    logger.info(f"{name} -> {result['count']} message(s) on {day}")
    return result["messages"]


def _run_for_day(start, end, day, me, logger) -> None:
    """Build and persist one day's page. Raises on failure — main() owns the run
    boundary and the alert, so a backfill is one run in the dashboard's history
    rather than N half-runs."""
    sent = _messages("fetch_sent_metadata", fetch_sent_metadata(start, end), day, logger)
    inbox = _messages("fetch_inbox_metadata", fetch_inbox_metadata(start, end), day, logger)
    if sent is None and inbox is None:
        # Both halves of the conversation are missing. A page built from that
        # would read as a day he spoke to nobody, which is a different day.
        return

    sent_rows = filter_noise(sent or [], me, logger=logger)
    inbox_rows = filter_inbound_noise(inbox or [], me, logger=logger)

    # Read BEFORE today is folded in. "First contact" and every age in days is
    # a claim about the state that existed before this run.
    known = load_threads()
    threads = group_day(sent_rows, inbox_rows, me)
    active = {t["thread_id"] for t in threads}
    quiet = quiet_threads(known, active, day)
    logger.info(f"{len(threads)} conversation(s) from {len(sent_rows)} sent and "
                f"{len(inbox_rows)} arrived message(s); {len(quiet)} gone quiet")

    if not threads and not quiet:
        logger.info(f"No correspondence on {day}; nothing to write")
        return

    persist_or_email(
        render_page(threads, known, day), "Correspondence", day,
        subject=f"Correspondence (needs manual paste) - {day:%Y-%m-%d}",
        task_name="daily_correspondence", logger=logger,
        directory=_correspondence_dir(),
    )
    # After the page, never before: the page states what was true at the start
    # of the run, and folding today in first would make every thread read as
    # already known and every age as zero.
    remember_threads(threads, day)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="write a single day YYYY-MM-DD")
    parser.add_argument("--backfill", type=int, default=0,
                        help="write each of the last N days; default 0 = just yesterday")
    args = parser.parse_args()

    logger = setup_logger("daily_correspondence")
    logger.info("Starting daily correspondence run")

    if registry.skip_if_disabled("daily_correspondence", logger):
        return 0

    try:
        # Identity, resolved once: every filter and every people list is "everyone
        # who is not him". An empty answer would make the whole day self-addressed
        # and drop it, so it is a hard failure rather than a quiet empty page.
        me = my_address()
        if not me:
            raise RuntimeError("could not read the mailbox owner's address from Gmail")
        logger.info(f"mailbox: {me}")

        if args.date:
            tz = ZoneInfo(local_timezone())
            start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=tz)
            end = start.replace(hour=23, minute=59, second=59)
            logger.info(f"Single day: {start.date()}")
            _run_for_day(start, end, start.date(), me, logger)
        elif args.backfill > 0:
            tz = ZoneInfo(local_timezone())
            today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            # Oldest day first, so the log reads chronologically as files land.
            for k in range(args.backfill, 0, -1):
                start, end, day = prior_day(today - timedelta(days=k - 1))
                logger.info(f"Backfill day {day}")
                _run_for_day(start, end, day, me, logger)
        else:
            start, end, day = prior_day()
            logger.info(f"Day: {day}")
            _run_for_day(start, end, day, me, logger)

        logger.info("Daily correspondence run complete")
        return 0
    except Exception as e:
        logger.exception(f"Daily correspondence run failed: {e}")
        notify_failure("daily_correspondence", e, logger)
        return 1


if __name__ == "__main__":
    sys.exit(main())
