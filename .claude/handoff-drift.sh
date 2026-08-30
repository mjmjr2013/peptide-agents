#!/usr/bin/env bash
# SessionStart hook: tell the session, up front, whether HANDOFF.md has fallen
# behind the code. Jordan should never have to remember to ask for a handoff
# update — the session is told it owes one before it does anything else.
#
# Emits SessionStart additionalContext when there is drift, and nothing at all
# when the handoff is current (silence = no noise on a clean start).
set -uo pipefail

cd "$(dirname "$0")/.." 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

HANDOFF="HANDOFF.md"
[ -f "$HANDOFF" ] || exit 0

# Last commit that actually touched the handoff.
last_handoff=$(git log -1 --format=%H -- "$HANDOFF" 2>/dev/null)
[ -n "$last_handoff" ] || exit 0

# Commits since then that changed anything OTHER than the handoff itself.
drift=$(git log "$last_handoff"..HEAD --format='  %h %s' 2>/dev/null)
drift_count=$(printf '%s' "$drift" | grep -c . || true)

# Uncommitted work in the tree (tracked files only).
dirty=$(git status --porcelain --untracked-files=no 2>/dev/null | grep -c . || true)

[ "$drift_count" -eq 0 ] && [ "$dirty" -eq 0 ] && exit 0

msg="HANDOFF STATUS — checked automatically at session start."
if [ "$drift_count" -gt 0 ]; then
  msg="$msg

${drift_count} commit(s) have landed since HANDOFF.md was last updated:
${drift}

HANDOFF.md is this project's canonical record (see CLAUDE.md). Bring it current as
part of this session — append to the highest-numbered section, or open a new one —
without waiting to be asked. Record WHY, not just what: the reasoning and the
deliberate choices are the part that is expensive to reconstruct."
fi
if [ "$dirty" -gt 0 ]; then
  msg="$msg

${dirty} tracked file(s) have uncommitted changes in the working tree."
fi

python3 - "$msg" <<'PY' 2>/dev/null || printf '%s\n' "{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"HANDOFF.md may be behind HEAD; check and update it.\"}}"
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": sys.argv[1],
}}))
PY
