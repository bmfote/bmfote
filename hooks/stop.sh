#!/bin/bash
# Cloud-native Stop hook for cctx.
# Fires when a Claude Code session ends — does one final sync to capture
# the last assistant response that wouldn't be caught by UserPromptSubmit.
#
# Requires: CCTX_URL and CCTX_TOKEN env vars (set by cctx installer)

# Skip when invoked from a recap-generation `claude -p` subprocess so those
# meta-recap runs don't get synced to cctx and don't recursively spawn another
# recap. Set by hooks/stop-recap.sh before invoking `claude -p`.
[ -n "${CCTX_SKIP_HOOKS:-}" ] && exit 0

# Load config — env vars take precedence, then config file
CCTX_CONFIG="$HOME/.claude/cctx.env"
if [ -f "$CCTX_CONFIG" ]; then
  . "$CCTX_CONFIG"
fi
CCTX_URL="${CCTX_URL:-}"
CCTX_TOKEN="${CCTX_TOKEN:-}"

if [ -z "$CCTX_URL" ] || [ -z "$CCTX_TOKEN" ]; then
  exit 0
fi

# Read hook input from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "")

if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Find the sync script (co-located with this hook in ~/.claude/hooks/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
SYNC_SCRIPT="$SCRIPT_DIR/cctx-sync-transcript.sh"
[ ! -f "$SYNC_SCRIPT" ] && SYNC_SCRIPT="$SCRIPT_DIR/sync-transcript.sh"

if [ -f "$SYNC_SCRIPT" ]; then
  # Run sync in foreground (session is ending, we want it to complete)
  "$SYNC_SCRIPT" "$SESSION_ID" "$TRANSCRIPT_PATH"
fi

# Kick off per-workspace recap in the background — claude -p takes a few
# seconds, so we detach to keep /exit snappy. The recap is only consumed by
# the next `cctx start`, which is always well after session-end.
RECAP_SCRIPT="$SCRIPT_DIR/cctx-stop-recap.sh"
[ ! -f "$RECAP_SCRIPT" ] && RECAP_SCRIPT="$SCRIPT_DIR/stop-recap.sh"
if [ -f "$RECAP_SCRIPT" ]; then
  ( "$RECAP_SCRIPT" "$SESSION_ID" "$TRANSCRIPT_PATH" < /dev/null > /dev/null 2>&1 & )
fi

# Kick off the definitions proposer in the background. No-op unless the
# project has a .cctx/tracked.txt manifest, so most sessions pay zero cost.
DEF_SCRIPT="$SCRIPT_DIR/cctx-stop-definitions.sh"
[ ! -f "$DEF_SCRIPT" ] && DEF_SCRIPT="$SCRIPT_DIR/stop-definitions.sh"
if [ -f "$DEF_SCRIPT" ] && [ -n "$CWD" ]; then
  ( "$DEF_SCRIPT" "$SESSION_ID" "$TRANSCRIPT_PATH" "$CWD" < /dev/null > /dev/null 2>&1 & )
fi
