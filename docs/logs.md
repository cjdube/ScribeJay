# Logs

*Split from LocalLLMAgent's `docs/logs.md` — that doc also covers Wren's `/logs`
web viewer, which has no ScribeJay counterpart (no web app here at all). This
file keeps only the part both agents share: the two-stream convention.*

ScribeJay has no log viewer. To read a run's log, open the file directly:

```bash
tail -f logs/daily_chrome_learnings.log
```

## The two streams

Every task run through `scribejay/core/logs.py:setup_logger` writes two files
per task, named after the task's module basename:

| Stream | File | What it is |
|---|---|---|
| structured log | `logs/<task>.log` | `setup_logger`'s file handler. Rotated at 2 MB, 3 backups. |
| launchd stdout | `logs/<task>.launchd.log` | launchd's `StandardOutPath`. Where a crash *before* the logger initialises lands. |

A run that never reaches `setup_logger` — a bad import, a missing env var read
at module load — leaves nothing in `<task>.log`. Check `<task>.launchd.log`
first when a job looks like it never ran at all.

## Bounds

`<task>.log` is capped at 2 MB × 3 backups (~8 MB) — `setup_logger` rotates it,
so it can't grow without bound. `<task>.launchd.log` has no rotation and grows
forever; nothing in this repo currently prunes it.

## Related

- [architecture.md](architecture.md) — the 8 tasks and their log basenames
