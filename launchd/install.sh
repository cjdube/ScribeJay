#!/bin/bash
# Install the ScribeJay launchd agents that are NOT generated.
#
# The eight scheduled task agents are no longer files. `scribejay schedule
# install` writes them from scribejay/core/registry.py, which means only the
# tasks whose sources the user actually wants get installed at all — see
# scribejay/cli/schedule.py.
#
# What is left here is local.scribejay.selfheal, and it stays a committed file
# on purpose: it runs under Apple-signed /bin/bash so that it still starts when
# a brew upgrade has left the python interpreter unexecutable, which is the
# exact failure it exists to repair. It is also the one agent that is specific
# to a source checkout — it heals `$ROOT/.venv/bin/python`, so it is only
# meaningful when there is a checkout with a venv.
#
# The committed plist carries a __SCRIBEJAY_ROOT__ placeholder instead of an
# absolute path — launchd expands neither ~ nor $HOME in ProgramArguments, so
# the substitution has to happen at install time.
#
#   ./launchd/install.sh                                        # all of them
#   ./launchd/install.sh launchd/local.scribejay.selfheal.plist # just this one
#
# Re-running is safe: an already-loaded agent is booted out first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

mkdir -p "$AGENTS"

if [ $# -gt 0 ]; then
    plists=("$@")
else
    plists=("$ROOT"/launchd/*.plist)
fi

for src in "${plists[@]}"; do
    name="$(basename "$src")"
    label="${name%.plist}"
    dest="$AGENTS/$name"

    sed "s|__SCRIBEJAY_ROOT__|$ROOT|g" "$src" > "$dest"

    # bootout, not unload: bootout is the modern verb and is what pairs with
    # bootstrap. Ignore its failure — an agent that isn't loaded yet is fine.
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$dest"
    echo "installed $label"
done

echo
echo "The eight scheduled tasks are installed separately, from the registry:"
echo "  scribejay schedule install"
