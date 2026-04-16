#!/bin/bash
# Cloud-native UserPromptSubmit hook for cctx.
# No local database required — talks directly to the Railway API.
#
# What it does:
# 1. Syncs new messages from local transcript to cloud (background, non-blocking)
# 2. Detects compaction events → injects conversation recovery from cloud
# 3. Injects recent session archive context for the current project
# 4. Prints API reminder so Claude knows memory is available
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

AUTH="Authorization: Bearer $CCTX_TOKEN"
MARKER_DIR="$HOME/.claude/hooks/.compaction-markers"
mkdir -p "$MARKER_DIR"

# Read hook input from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")

# Quick health check (fail-open: if API is down or token is bad, don't block)
if ! curl -sf --connect-timeout 1 -H "$AUTH" "$CCTX_URL/api/stats" > /dev/null 2>&1; then
  exit 0
fi

# --- Sync transcript to cloud (background, non-blocking) ---
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")

if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] && [ -n "$SESSION_ID" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
  SYNC_SCRIPT="$SCRIPT_DIR/cctx-sync-transcript.sh"
  [ ! -f "$SYNC_SCRIPT" ] && SYNC_SCRIPT="$SCRIPT_DIR/sync-transcript.sh"
  if [ -f "$SYNC_SCRIPT" ]; then
    "$SYNC_SCRIPT" "$SESSION_ID" "$TRANSCRIPT_PATH" &
  fi
fi

# --- Compaction detection ---
COMPACTION_DIR="$HOME/.claude/projects"
if [ -n "$SESSION_ID" ]; then
  CURRENT_COUNT=$(find "$COMPACTION_DIR" -path "*/$SESSION_ID/subagents/agent-acompact-*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
  CURRENT_COUNT=${CURRENT_COUNT:-0}
  MARKER_FILE="$MARKER_DIR/$SESSION_ID"

  PREV_COUNT=0
  if [ -f "$MARKER_FILE" ]; then
    PREV_COUNT=$(cat "$MARKER_FILE" 2>/dev/null || echo "0")
  fi

  if [ "${CURRENT_COUNT:-0}" -gt "${PREV_COUNT:-0}" ] 2>/dev/null; then
    echo "$CURRENT_COUNT" > "$MARKER_FILE"

    RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
      "$CCTX_URL/api/recent?hours=8&limit=40&session_id=$SESSION_ID" 2>/dev/null)

    if [ -z "$RECENT" ] || [ "$RECENT" = "[]" ]; then
      RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
        "$CCTX_URL/api/recent?hours=8&limit=40" 2>/dev/null)
    fi

    if [ -n "$RECENT" ] && [ "$RECENT" != "[]" ]; then
      CONTEXT=$(echo "$RECENT" | python3 -c "
import sys, json
messages = json.load(sys.stdin)
messages.reverse()
lines = ['CONTEXT RECOVERY — Compaction detected. Recent messages from memory database:', '']
for m in messages:
    role = m.get('role', m.get('type', '?'))
    content = (m.get('content') or '')[:600]
    ts = m.get('timestamp', '')[:19]
    project = m.get('project', '')
    lines.append(f'[{ts}] ({project}) {role}: {content}')
    lines.append('---')
print('\n'.join(lines))
" 2>/dev/null)
      if [ -n "$CONTEXT" ]; then
        echo "$CONTEXT"
        exit 0
      fi
    fi
  fi
fi

# --- Normal message: project context + API reminder ---

PROJECT=$(echo "$INPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tp = data.get('transcript_path', '')
parts = tp.split('/projects/')
if len(parts) > 1:
    project_dir = parts[1].split('/')[0]
    if 'github_projects-' in project_dir:
        print(project_dir.split('github_projects-')[-1])
    elif project_dir.startswith('-Users-'):
        print('home')
    else:
        print(project_dir)
else:
    print('')
" 2>/dev/null || echo "")

PROJECT_MSGS=$(curl -s --connect-timeout 2 --max-time 3 -H "$AUTH" \
  "$CCTX_URL/api/project/$PROJECT?limit=3" 2>/dev/null)

SESSION_CONTEXT=$(echo "$PROJECT_MSGS" | python3 -c "
import sys, json
msgs = json.load(sys.stdin)
if msgs:
    lines = [f'Recent activity in {\"$PROJECT\" or \"this project\"}:']
    for m in msgs:
        ts = (m.get('timestamp') or '')[:10]
        content = (m.get('content') or '')[:100].replace('\n', ' ')
        lines.append(f'  [{ts}] {m.get(\"type\",\"?\")}: {content}')
    print('\n'.join(lines))
" 2>/dev/null)

echo "Cloud context available. MCP tools: search_memory (FTS5, <100ms), find_error (past errors+solutions), get_context (by UUID), get_recent (last N hours), remember (write). Shell fallback: source ~/.claude/cctx.env && curl -s -H \"Authorization: Bearer \$CCTX_TOKEN\" \"\$CCTX_URL/api/search?q=QUERY\""
if [ -n "$SESSION_CONTEXT" ]; then
  echo "$SESSION_CONTEXT"
fi
