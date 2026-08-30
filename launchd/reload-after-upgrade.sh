#!/bin/bash
# Reload ScribeJay's launchd agents after a Homebrew Python upgrade.
#
# `brew upgrade python@3.12` deletes the old Cellar directory, and launchd
# refuses to exec the replacement: it cached the old interpreter's code
# signature. Jobs die at launch with OS_REASON_CODESIGNING (exit -9) *before*
# running any of our code — so no log line, and notify_failure never gets the
# chance to push. The job simply appears not to have run.
#
# Run this after any brew upgrade that touches python:
#
#   ./launchd/reload-after-upgrade.sh           # heal whatever is stale
#   ./launchd/reload-after-upgrade.sh --check   # report only; exit 1 if stale
#   ./launchd/reload-after-upgrade.sh --quiet   # say nothing unless it acts
#
# Safe to run any time: it reloads only what is actually stale, and skips jobs
# that are mid-run rather than killing work in progress. install.sh also clears
# this as a side effect of reloading everything — this script exists because it
# tells you whether you needed it, and won't interrupt a running job to do it.
#
# Staleness is judged two ways, and the first is why the second is not enough:
#
#   proactive — the interpreter's identity (realpath + inode + mtime) is
#     recorded in config/.interpreter_id. When it changes, EVERY agent is
#     reloaded, because every one of them is now carrying a signature launchd
#     will reject. This is the check that matters.
#   reactive  — "needs LWCR update" on an individual job. launchd only sets
#     that flag AFTER a job has tried to exec and failed, so on its own it can
#     never prevent a missed run, only notice one. Kept as a backstop for
#     breakage that isn't an interpreter swap.
#
# The reactive check alone lost a run of every job, every upgrade. A job that
# hadn't fired since the upgrade carried no flag, so a reactive-only script
# declared it healthy and walked past it; it stayed broken until its next
# scheduled fire, and that fire was the one that died. That cost Wren a day of
# every daily job and a full week of its two Sunday jobs on 2026-08-13.
#
# This is ScribeJay's copy. Wren has its own, guarding local.wren.* against
# Wren's own interpreter. Neither touches the other's agents: a checkout can be
# upgraded, moved or removed on its own, and the fingerprint that decides
# staleness is per-checkout. Two differences from Wren's copy, both because
# ScribeJay is only ever a set of scheduled jobs:
#
#   * no server. Wren's copy also watches its KeepAlive chat server, which is
#     stale whenever the interpreter it exec'd is gone. ScribeJay has no
#     long-running process.
#   * no catch-up queue. Wren hands repaired labels to tasks.startup_recovery so
#     the missed runs are serialized behind the single Ollama slot. ScribeJay
#     has no such runner; a missed journaling day is recovered by running the
#     task by hand with --day, which is the honest answer rather than starting
#     eight model jobs at once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
INTERPRETER_ID="$ROOT/config/.interpreter_id"

CHECK_ONLY=0
QUIET=0
case "${1:-}" in
    --check) CHECK_ONLY=1 ;;
    --quiet) QUIET=1 ;;
    "")      ;;
    *)       echo "usage: $(basename "$0") [--check|--quiet]" >&2; exit 2 ;;
esac

# A repair is a real event — an upgrade silently broke the schedule and this put
# it back. Push it, because the alternative is finding out from whatever job
# didn't run. Never fail the repair just because the push didn't go out.
#
# Wren's copy shells out to `-m agent.tools.notify`, which has a CLI because it
# is also a chat tool. ScribeJay's notify is a plain function — no tool
# registry, no CLI — so call it directly. Arguments go through argv, never
# through the interpolated source, so a message with a quote in it can't break
# the snippet.
push() {
    [ -x "$ROOT/.venv/bin/python" ] || return 0
    (cd "$ROOT" && "$ROOT/.venv/bin/python" -c \
        'import sys; from scribejay.core.notify import notify; notify(sys.argv[1], title=sys.argv[2])' \
        "$1" "ScribeJay: agents reloaded after an upgrade") >/dev/null 2>&1 || true
}

# launchd reports the cached-signature mismatch as "needs LWCR update" (launch
# weak code requirement). That is the authoritative signal — an exit status of
# -9 alone can't distinguish it from any other kill.
needs_reload() {
    launchctl print "$DOMAIN/$1" 2>/dev/null | grep -q "needs LWCR update"
}

is_running() {
    launchctl print "$DOMAIN/$1" 2>/dev/null | grep -qE '^[[:space:]]*state = running'
}

# Identity of the interpreter every plist execs. Resolved to its real Cellar
# path and stamped with inode and mtime, so a brew upgrade that lands a new
# binary on the SAME path is still detected — the symlink chain out of
# .venv/bin/python doesn't change on every upgrade, but the file it lands on
# always does. Empty output (missing interpreter) is treated as "unknown" and
# never overwrites a good recorded value.
fingerprint() {
    local real
    real=$(/usr/bin/readlink -f "$ROOT/.venv/bin/python" 2>/dev/null) || return 0
    [ -n "$real" ] && /usr/bin/stat -f '%N %i %m' "$real" 2>/dev/null
}

# Does this agent actually exec the interpreter that changed? Every ScribeJay
# agent does today, but the check is kept so a future non-Python agent isn't
# swept up. Read from ProgramArguments rather than grepping the file: a plist
# comment naming the interpreter would match a plain grep.
uses_interpreter() {
    [ "$(/usr/bin/plutil -extract ProgramArguments.0 raw -o - "$AGENTS/$1.plist" 2>/dev/null)" \
      = "$ROOT/.venv/bin/python" ]
}

# `|| true` on both: a missing interpreter or a missing record must leave these
# empty, not abort the run under `set -e`. A vanished .venv is precisely when
# this script has to keep working.
now_fp="$(fingerprint || true)"
was_fp="$(cat "$INTERPRETER_ID" 2>/dev/null || true)"

# Missing baseline means this is the first run since the check was added (or a
# fresh install, where install.sh has just bootstrapped everything). Seed it
# rather than reloading agents that are already healthy — otherwise every new
# checkout opens with a spurious "reloaded everything" push.
seeding=0
if [ -z "$was_fp" ]; then
    seeding=1
fi

# An unreadable interpreter can't be compared against anything. Say nothing and
# leave the recorded value alone; the reactive check below still applies.
interpreter_changed=0
if [ -n "$now_fp" ] && [ "$seeding" -eq 0 ] && [ "$now_fp" != "$was_fp" ]; then
    interpreter_changed=1
fi

stale=()
for dest in "$AGENTS"/local.scribejay.*.plist; do
    [ -e "$dest" ] || continue
    label="$(basename "$dest" .plist)"
    if needs_reload "$label"; then
        stale+=("$label")
    elif [ "$interpreter_changed" -eq 1 ] && uses_interpreter "$label"; then
        stale+=("$label")
    fi
done

if [ ${#stale[@]} -eq 0 ]; then
    # Record the interpreter we just certified everything against. Never on
    # --check: that flag must not change state, or the next real run would
    # think it had already healed the upgrade.
    if [ "$CHECK_ONLY" -eq 0 ] && [ -n "$now_fp" ] && [ "$now_fp" != "$was_fp" ]; then
        printf '%s\n' "$now_fp" > "$INTERPRETER_ID"
        [ "$QUIET" -eq 1 ] || echo "recorded interpreter fingerprint"
    fi
    [ "$QUIET" -eq 1 ] || echo "nothing stale — every agent matches the current interpreter"
    exit 0
fi

# Timestamped because this log accumulates a handful of rare events over months;
# "reloaded dailycommits" is only useful next to the upgrade that caused it.
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

if [ "$interpreter_changed" -eq 1 ]; then
    log "interpreter changed: ${was_fp:-<none>} -> $now_fp"
fi
for label in "${stale[@]}"; do
    log "stale: $label"
done

if [ "$CHECK_ONLY" -eq 1 ]; then
    exit 1
fi

# `launchctl bootout` is asynchronous: it returns as soon as the teardown is
# queued, while the service is still leaving the domain. Bootstrapping into a
# domain that still holds it fails with "Input/output error" (5), which under
# `set -e` would end the run with the agent booted OUT rather than reloaded. A
# healer must never finish with fewer services than it started with. So: wait
# for the service to actually go, then retry the bootstrap.
#
# Returns non-zero if the agent could not be brought back. Callers MUST test it
# — never call this bare under `set -e`.
reload() {
    local label="$1" i
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    for i in $(seq 1 20); do
        launchctl print "$DOMAIN/$label" >/dev/null 2>&1 || break
        sleep 0.5
    done
    for i in 1 2 3; do
        if launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist" 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    # One last attempt with stderr shown, so the log says why it failed.
    launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist" || return 1
}

skipped=0
healed=""
failed=""
for label in "${stale[@]}"; do
    # A job caught mid-run is doing real work — a summarizer can hold the single
    # Ollama slot for many minutes. Its next run is already broken; one more
    # missed run costs less than a killed one.
    if is_running "$label"; then
        log "  skipped $label — mid-run, rerun this once it finishes"
        skipped=1
        continue
    fi
    if reload "$label"; then
        healed="$healed ${label#local.scribejay.}"
        log "  reloaded $label"
    else
        failed="$failed ${label#local.scribejay.}"
        log "  FAILED to reload $label — it is not loaded right now"
    fi
done

# Record the interpreter only once every agent is actually back on it. Writing
# it while something is still stale would mark the upgrade handled, and the next
# pass would skip the repair entirely — the one outcome worse than the bug this
# script fixes.
if [ -n "$now_fp" ] && [ "$now_fp" != "$was_fp" ] \
   && [ -z "$failed" ] && [ "$skipped" -eq 0 ]; then
    printf '%s\n' "$now_fp" > "$INTERPRETER_ID"
fi

[ -n "$healed" ] && push "Reloaded after an interpreter change:$healed"

# A failed reload means a service is loaded nowhere. Push it on its own — it is
# the one outcome that needs a human, and it must not be buried under the list
# of things that did work.
if [ -n "$failed" ]; then
    push "COULD NOT RELOAD:$failed"
    log "failed to reload:$failed"
    exit 1
fi

if [ "$skipped" -eq 1 ]; then
    log "some agents were mid-run and still need a reload"
    exit 1
fi
log "done"
