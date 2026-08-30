#!/bin/bash
# Install ScribeJay's launchd agents.
#
# The committed plists carry a __SCRIBEJAY_ROOT__ placeholder instead of an
# absolute path — launchd expands neither ~ nor $HOME in ProgramArguments, so
# the substitution has to happen at install time. This fills it in from where
# the repo actually sits and bootstraps each agent.
#
#   ./launchd/install.sh                                        # all agents
#   ./launchd/install.sh launchd/local.scribejay.dailycommits.plist   # just this one
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
