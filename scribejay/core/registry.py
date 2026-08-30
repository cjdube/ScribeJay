"""Every scheduled task, and whether it should run today.

One table. It is what `scribejay/core/features.py` is for — a feature answers
"does the user want this material", and this answers "which jobs need it".
Later phases read the same table rather than growing their own copies: the
launchd plists are generated from it (Phase 5) and `doctor` reports against it
(Phase 6).

The rule a disabled task must follow is not obvious, and AGENTS.md is explicit
about why: a dashboard builds run history from the *log lines*, never from exit
codes. So a skipped run still logs its "run complete" boundary. Without it the
task reads as **hung**, not as **off**, and a user who turned Strava off to stop
being nagged would get a different flavour of the same nagging.

It must also not call `notify_failure`. Turning a source off is the user's
decision; a push telling them about it is the bug this whole phase exists to
fix.
"""

from dataclasses import dataclass

from scribejay.core import features


@dataclass(frozen=True)
class Task:
    """One launchd job.

    `requires` is every feature the task cannot run without — all of them, so a
    task needing both Strava and a calendar to write to is skipped when either
    is off. Features a task merely *enriches* with (ClickUp in `daily_commits`)
    are deliberately not listed: the task still has work to do without them.

    `complete_line` is the exact boundary line the task logs on a successful
    run, repeated by `skip_if_disabled` so the log looks the same shape whether
    the run did work or not. `tests/test_registry.py` asserts each one appears
    verbatim in its own module, so the copy here cannot drift from the original.
    """

    key: str
    label: str
    requires: tuple[str, ...]
    hour: int
    minute: int
    complete_line: str


TASKS: tuple[Task, ...] = (
    Task("ai_chat_learnings", "AI chat learnings", ("transcripts",),
         4, 30, "AI chat learnings run complete"),
    Task("claude_time_blocks", "AI Session Time Blocks",
         ("transcripts", "google_calendar"),
         4, 45, "AI Session Time Blocks run complete"),
    Task("daily_commits", "Daily commits", ("git",),
         4, 55, "Daily commits run complete"),
    Task("daily_youtube_learnings", "Daily YouTube learnings", ("youtube",),
         5, 5, "Daily youtube learnings run complete"),
    Task("daily_chrome_learnings", "Daily Chrome learnings", ("chrome",),
         5, 15, "Daily chrome learnings run complete"),
    Task("daily_correspondence", "Daily correspondence", ("gmail",),
         5, 20, "Daily correspondence run complete"),
    Task("strava_download", "Strava download", ("strava", "google_calendar"),
         5, 50, "Strava download run complete"),
    Task("calendar_colorizer", "Calendar colorizer", ("google_calendar",),
         17, 0, "Colorizer run complete: 0 updated, 0 skipped"),
)

BY_KEY: dict[str, Task] = {t.key: t for t in TASKS}

KEYS: tuple[str, ...] = tuple(t.key for t in TASKS)


def get(key: str) -> Task:
    task = BY_KEY.get(key)
    if task is None:
        raise KeyError(f"unknown task: {key}")
    return task


def is_ready(key: str) -> tuple[bool, str]:
    """(ready, reason). Reports the *first* missing feature rather than all of
    them: a user fixes one thing at a time, and the next run names the next."""
    for name in get(key).requires:
        ok, reason = features.state(name)
        if not ok:
            return False, reason
    return True, ""


def skip_if_disabled(key: str, logger) -> bool:
    """The guard every task's main() calls right after setup_logger.

    Returns True when the caller should `return 0` immediately. Owns the
    boundary line so no task has to remember the rule, and logs at INFO — a
    disabled task is a normal state, not a problem to be found later by
    someone grepping for warnings.
    """
    ready, reason = is_ready(key)
    if ready:
        return False
    task = get(key)
    logger.info(f"Skipping: {reason}")
    logger.info(task.complete_line)
    return True


def enabled_tasks() -> list[Task]:
    """The tasks worth scheduling. Phase 5's plist generator reads this."""
    return [t for t in TASKS if is_ready(t.key)[0]]
