"""Print what is on and what is off.

A stopgap until `scribejay doctor` (Phase 6) does this properly. Reads the same
two tables every task reads, so what it prints is what a 4:30 AM run will do.
"""

from scribejay.core import config, features, registry


def main() -> int:
    print(f"settings file : {config.config_path()}")
    print(f"journal folder: {config.getenv('LEARNINGS_DIR')}")
    print(f"model backend : {config.getenv('SCRIBEJAY_LLM_BACKEND') or 'ollama (unset)'}")
    print(f"local model   : {config.getenv('OLLAMA_MODEL')}")

    print("\nSOURCES")
    for f in features.FEATURES:
        on, why = features.state(f.name)
        print(f"  {'on ' if on else 'OFF'}  {f.name:17} {why}")

    print("\nJOBS")
    for task in registry.TASKS:
        ok, why = registry.is_ready(task.key)
        when = f"{task.hour:02d}:{task.minute:02d}"
        print(f"  {'runs   ' if ok else 'SKIPPED'} {when}  {task.key:25} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
