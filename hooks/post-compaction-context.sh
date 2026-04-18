#!/bin/bash
# Cloud-native UserPromptSubmit hook for cctx.
# No local database required — talks directly to the Railway API.
#
# What it does:
# 1. Resolves the current workspace (CCTX_WORKSPACE env → transcript-path project → cctx-default)
# 2. Syncs new messages from local transcript to cloud, tagged with workspace_id (background, non-blocking)
# 3. Detects compaction events → injects conversation recovery from cloud
# 4. Injects current workspace + known workspaces list + recent workspace activity
# 5. Prints API reminder so Claude knows memory is available
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

TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")

# --- Resolve workspace_id ---
# Priority: CCTX_WORKSPACE env → transcript-path project derivation → cctx-default
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

WORKSPACE_ID="${CCTX_WORKSPACE:-${PROJECT:-cctx-default}}"
export CCTX_WORKSPACE="$WORKSPACE_ID"

# --- Sync transcript to cloud (background, non-blocking) ---
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] && [ -n "$SESSION_ID" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
  SYNC_SCRIPT="$SCRIPT_DIR/cctx-sync-transcript.sh"
  [ ! -f "$SYNC_SCRIPT" ] && SYNC_SCRIPT="$SCRIPT_DIR/sync-transcript.sh"
  if [ -f "$SYNC_SCRIPT" ]; then
    "$SYNC_SCRIPT" "$SESSION_ID" "$TRANSCRIPT_PATH" "$WORKSPACE_ID" &
  fi
fi

# --- Compaction detection ---
# Two detection paths: (1) JSONL summary record, which is how Claude Code
# signals native /compact in the transcript; (2) legacy agent-acompact-*.jsonl
# files from older releases. Count both so we catch compaction regardless of
# which mechanism fired.
COMPACTION_DIR="$HOME/.claude/projects"
if [ -n "$SESSION_ID" ] && [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  FILE_COUNT=$(find "$COMPACTION_DIR" -path "*/$SESSION_ID/subagents/agent-acompact-*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
  FILE_COUNT=${FILE_COUNT:-0}

  JSONL_COUNT=$(python3 -c "
import sys, json
path = sys.argv[1]
count = 0
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get('type') != 'user':
                continue
            msg = r.get('message') or {}
            body = msg.get('content')
            hit = False
            if isinstance(body, str) and 'This session is being continued from a previous conversation' in body:
                hit = True
            elif isinstance(body, list):
                for block in body:
                    if isinstance(block, dict) and block.get('type') == 'text' and 'This session is being continued from a previous conversation' in (block.get('text') or ''):
                        hit = True
                        break
            if hit:
                count += 1
except Exception:
    pass
print(count)
" "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
  JSONL_COUNT=${JSONL_COUNT:-0}

  CURRENT_COUNT=$((FILE_COUNT + JSONL_COUNT))
  MARKER_FILE="$MARKER_DIR/$SESSION_ID"

  PREV_COUNT=0
  if [ -f "$MARKER_FILE" ]; then
    PREV_COUNT=$(cat "$MARKER_FILE" 2>/dev/null || echo "0")
  fi

  if [ "${CURRENT_COUNT:-0}" -gt "${PREV_COUNT:-0}" ] 2>/dev/null; then
    echo "$CURRENT_COUNT" > "$MARKER_FILE"

    RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
      "$CCTX_URL/api/recent?hours=8&limit=40&session_id=$SESSION_ID&workspace_id=$WORKSPACE_ID" 2>/dev/null)

    if [ -z "$RECENT" ] || [ "$RECENT" = "[]" ]; then
      RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
        "$CCTX_URL/api/recent?hours=8&limit=40&workspace_id=$WORKSPACE_ID" 2>/dev/null)
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

# --- Normal message: workspace-scoped context + API reminder ---

# Fetch known workspaces so Claude can recognize cross-workspace natural-language queries
KNOWN_WS=$(curl -s --connect-timeout 2 --max-time 3 -H "$AUTH" \
  "$CCTX_URL/api/workspaces?limit=20" 2>/dev/null)

KNOWN_WS_LINE=$(echo "$KNOWN_WS" | python3 -c "
import sys, json
try:
    wss = json.load(sys.stdin)
except Exception:
    wss = []
names = [w.get('workspace_id') for w in wss if w.get('workspace_id')]
if names:
    print('Known workspaces: ' + ', '.join(names))
" 2>/dev/null)

# Fetch last 3 prior sessions for this workspace (excluding current) to power
# the session-start recap. Only runs when we have a real session_id — without
# one we can't exclude the current session and would recap ourselves.
PRIOR_SESSIONS_BLOCK=""
if [ -n "$SESSION_ID" ]; then
  PRIOR_JSON=$(curl -s --connect-timeout 2 --max-time 3 -H "$AUTH" \
    "$CCTX_URL/api/sessions?workspace_id=$WORKSPACE_ID&limit=3&exclude_session_id=$SESSION_ID" 2>/dev/null)

  PRIOR_SESSIONS_BLOCK=$(echo "$PRIOR_JSON" | python3 -c "
import sys, json
from datetime import datetime, timezone

try:
    sessions = json.load(sys.stdin)
except Exception:
    sessions = []

if not isinstance(sessions, list):
    sessions = []

if not sessions:
    print('PRIOR_SESSIONS: none (first time in this workspace)')
    sys.exit(0)

def age(ts):
    if not ts:
        return '?'
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        hours = delta.seconds // 3600
        if days >= 1:
            return f'{days}d ago'
        if hours >= 1:
            return f'{hours}h ago'
        return f'{delta.seconds // 60}m ago'
    except Exception:
        return ts[:10]

lines = ['PRIOR_SESSIONS (most recent first — call get_recent(session_id=#1) for the recap):']
for i, s in enumerate(sessions, 1):
    sid = s.get('session_id', '?')
    a = age(s.get('last_timestamp'))
    n = s.get('message_count', 0)
    topic = (s.get('first_user_message') or '').replace('\n', ' ').strip()[:120]
    cont = s.get('continuation_of')
    if cont:
        lines.append(f'  {i}. {sid} — {a}, {n} msgs — continuation of {cont}')
    else:
        lines.append(f'  {i}. {sid} — {a}, {n} msgs — \"{topic}\"')
print('\n'.join(lines))
" 2>/dev/null)
fi

echo "Cloud context available. Current workspace: $WORKSPACE_ID. MCP tools do NOT auto-scope — pass workspace=\"$WORKSPACE_ID\" on search_memory/find_error/remember calls (get_recent with session_id auto-resolves). MCP tools: search_memory, find_error, get_context, get_recent, remember. Shell fallback: source ~/.claude/cctx.env && curl -s -H \"Authorization: Bearer \$CCTX_TOKEN\" \"\$CCTX_URL/api/search?q=QUERY&workspace_id=$WORKSPACE_ID\""
if [ -n "$KNOWN_WS_LINE" ]; then
  echo "$KNOWN_WS_LINE"
fi
if [ -n "$PRIOR_SESSIONS_BLOCK" ]; then
  echo "$PRIOR_SESSIONS_BLOCK"
fi
