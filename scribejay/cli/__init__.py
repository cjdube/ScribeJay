"""The `scribejay` command.

    scribejay init                      first-run wizard
    scribejay settings                  open the settings screen in a browser
    scribejay status                    what is on and what is off
    scribejay run <task> [args...]      run one task now
    scribejay schedule install|remove|status
    scribejay doctor [--probe]          why is nothing appearing?
    scribejay migrate [--dry-run]       move an old config/.env into place

Every subcommand is a thin wrapper over a module that already existed and can
still be called directly: `python -m scribejay.daily_commits --date ...` works
exactly as before, and `scribejay run daily_commits --date ...` is the same
call. That is deliberate. The launchd jobs, the docs, and a decade of muscle
memory all use the module form, and a console script that quietly became the
only supported way in would break every one of them.

Task arguments are passed through untouched: `run` takes the task key and hands
the rest of the command line to that task's own argparse, so a task can grow a
flag without this module learning about it.
"""

import argparse
import importlib
import sys

from scribejay.core import registry


def _run_task(key: str, extra: list[str]) -> int:
    """Import one task module and call its main(), with `extra` as its argv.

    The task parses sys.argv itself (each one has its own --date/--backfill),
    so the argument list is swapped in around the call rather than being
    threaded through a signature none of the eight share. `--help` after the
    task name is the one thing that does not reach it: argparse answers that
    here. `python -m scribejay.<task> --help` still shows the task's own.
    """
    module = importlib.import_module(f"scribejay.{key}")
    saved = sys.argv
    sys.argv = [f"scribejay.{key}", *extra]
    try:
        return module.main()
    finally:
        sys.argv = saved


def _cmd_run(args, extra: list[str]) -> int:
    return _run_task(args.task, extra)


def _cmd_settings(args, extra: list[str]) -> int:
    from scribejay.cli import settings_server

    return settings_server.serve(open_browser=not args.no_browser,
                                 idle_timeout=args.idle_timeout)


def _cmd_schedule(args, extra: list[str]) -> int:
    from scribejay.cli import schedule

    return {
        "install": schedule.install,
        "remove": schedule.remove,
        "status": schedule.status,
    }[args.action]()


def _cmd_init(args, extra: list[str]) -> int:
    from scribejay.cli import init

    return init.main([])


def _cmd_doctor(args, extra: list[str]) -> int:
    from scribejay.cli import doctor

    return doctor.main(["--probe"] if args.probe else [])


def _cmd_status(args, extra: list[str]) -> int:
    from scribejay import status

    return status.main()


def _cmd_migrate(args, extra: list[str]) -> int:
    from scribejay import migrate

    saved = sys.argv
    sys.argv = ["scribejay.migrate", *(["--dry-run"] if args.dry_run else [])]
    try:
        return migrate.main()
    finally:
        sys.argv = saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scribejay",
        description="ScribeJay — a local-first journaling agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="first-run setup wizard")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("settings", help="open the settings screen in a browser")
    p.add_argument("--no-browser", action="store_true",
                   help="print the URL instead of opening it")
    p.add_argument("--idle-timeout", type=int, default=900,
                   help="seconds of inactivity before the server stops "
                        "(default 900), so a forgotten tab does not leave a "
                        "port open")
    p.set_defaults(func=_cmd_settings)

    p = sub.add_parser("status", help="print which sources and jobs are on")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser(
        "run", help="run one task now",
        description="Arguments after the task name go to the task itself, "
                    "e.g. `scribejay run daily_commits --date 2026-08-29`.")
    p.add_argument("task", choices=list(registry.KEYS), metavar="TASK",
                   help="one of: " + ", ".join(registry.KEYS))
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("schedule", help="manage the launchd jobs")
    p.add_argument("action", choices=("install", "remove", "status"))
    p.set_defaults(func=_cmd_schedule)

    p = sub.add_parser("doctor", help="explain why a page or event is missing")
    p.add_argument("--probe", action="store_true",
                   help="also call each switched-on source for real")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("migrate", help="move an old config/.env into the settings file")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and change nothing")
    p.set_defaults(func=_cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # parse_known_args, not parse_args: `run <task>` forwards its tail to the
    # task's own parser, and a strict parse here would reject a flag this
    # module has no reason to know about. Every other subcommand rejects an
    # unknown argument itself, below, so the looseness stays where it is wanted.
    args, extra = build_parser().parse_known_args(argv)
    if extra and args.command != "run":
        print(f"unrecognized arguments: {' '.join(extra)}", file=sys.stderr)
        return 2
    return args.func(args, extra)
