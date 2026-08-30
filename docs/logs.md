# Logs

ScribeJay has no log viewer and no web app. To read a run's log, open the file directly:

```bash
tail -f ~/.scribejay/logs/daily_chrome_learnings.log
```

Logs live in `~/.scribejay/logs/` by default. They used to live in `logs/`
beside the repo, which stops being a place anything can write once ScribeJay is
installed as a tool — site-packages is not a log directory. Set
`SCRIBEJAY_LOGS_DIR` to put them somewhere else; a value you already had set is
still honoured. **The file names did not move with the directory**, and must
not: run history is keyed off the basenames.

## The two streams

Every task run through `scribejay/core/logs.py:setup_logger` writes two files
per task, named after the task's module basename:

| Stream | File | What it is |
|---|---|---|
| structured log | `<logs dir>/<task>.log` | `setup_logger`'s file handler. Rotated at 2 MB, 3 backups. |
| launchd stdout | `<logs dir>/<task>.launchd.log` | launchd's `StandardOutPath`. Where a crash *before* the logger initialises lands. |

A run that never reaches `setup_logger` — a bad import, a missing env var read
at module load — leaves nothing in `<task>.log`. Check `<task>.launchd.log`
first when a job looks like it never ran at all.

## Bounds

`<task>.log` is capped at 2 MB × 3 backups (~8 MB) — `setup_logger` rotates it,
so it can't grow without bound. `<task>.launchd.log` has no rotation and grows
forever; nothing in this repo currently prunes it.

## Related

- [architecture.md](architecture.md) — the 8 tasks and their log basenames
- [cli.md](cli.md) — `scribejay schedule`, which writes the `StandardOutPath`
  in each generated plist
