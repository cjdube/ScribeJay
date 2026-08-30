"""`python -m scribejay.cli`, the same entrypoint as the `scribejay` command.

Worth having for the case the console script is the thing that is broken — a
half-finished install, a PATH that does not include the tool's bin directory —
because everything the CLI can tell you about that is reachable this way.
"""

import sys

from scribejay.cli import main

if __name__ == "__main__":
    sys.exit(main())
