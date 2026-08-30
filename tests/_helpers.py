"""Shared test-only helpers with no production counterpart.

ScribeJay has no dashboard of its own to parse its logs (that's Wren's
chat/insights.py, which reads LocalLLMAgent's tasks/*.py logs) — but every
task here still follows the same "Starting X run" / "X run complete" logging
convention (AGENTS.md), so tests still verify the boundary is written. Mirrors
the matching half of LocalLLMAgent's chat/insights.py: _LINE_RE, _is_run_start,
_is_run_success.
"""

import re

LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) \[(\w+)\] (.*)$")


def is_run_start(msg: str) -> bool:
    low = msg.lower()
    return low.startswith("starting ") and ("run" in low or "rerun" in low)


def is_run_success(msg: str) -> bool:
    # Boundary messages never contain "->" (that marks tool call/result lines),
    # which keeps a tool result that happens to say "complete" from matching.
    if "->" in msg:
        return False
    low = msg.lower()
    return "complete" in low and ("run" in low or "rerun" in low)
