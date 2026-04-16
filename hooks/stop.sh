#!/bin/bash
# Cloud-native Stop hook for cctx.
# Fires when a Claude Code session ends — does one final sync to capture
# the last assistant response that wouldn't be caught by UserPromptSubmit.
#
# Requires: CCTX_URL and CCTX_TOKEN env vars (set by cctx installer)

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
