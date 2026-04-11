#!/bin/bash
# Cloud-native UserPromptSubmit hook for bmfote.
# No local database required — talks directly to the Railway API.
#
# What it does:
# 1. Syncs new messages from local transcript to cloud (background, non-blocking)
# 2. Detects compaction events → injects conversation recovery from cloud
# 3. Injects recent session archive context for the current project
# 4. Prints API reminder so Claude knows memory is available
#
# Requires: BMFOTE_URL and BMFOTE_TOKEN env vars (set by bmfote installer)

BMFOTE_URL="${BMFOTE_URL:-}"
BMFOTE_TOKEN="${BMFOTE_TOKEN:-}"

if [ -z "$BMFOTE_URL" ] || [ -z "$BMFOTE_TOKEN" ]; then
  exit 0
fi

AUTH="Authorization: Bearer $BMFOTE_TOKEN"
MARKER_DIR="$HOME/.claude/hooks/.compaction-markers"
mkdir -p "$MARKER_DIR"

# Read hook input from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")

# Quick health check (fail-open: if API is down or token is bad, don't block)
if ! curl -sf --connect-timeout 1 -H "$AUTH" "$BMFOTE_URL/api/stats" > /dev/null 2>&1; then
  exit 0
fi

# --- Sync transcript to cloud (background, non-blocking) ---
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
SYNC_MARKER_DIR="$HOME/.claude/hooks/.sync-markers"
mkdir -p "$SYNC_MARKER_DIR"

if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] && [ -n "$SESSION_ID" ]; then
  (
    set +e +o pipefail  # don't abort on errors in background sync
    export BMFOTE_URL BMFOTE_TOKEN
    export BMFOTE_SESSION_ID="$SESSION_ID"
    SYNC_MARKER="$SYNC_MARKER_DIR/$SESSION_ID"
    SYNCED_LINES=0
    if [ -f "$SYNC_MARKER" ]; then
      SYNCED_LINES=$(cat "$SYNC_MARKER" 2>/dev/null || echo "0")
    fi
    TOTAL_LINES=$(wc -l < "$TRANSCRIPT_PATH" | tr -d ' ')

    # On first sync, only grab the last 200 lines (not entire backlog)
    if [ "$SYNCED_LINES" -eq 0 ] && [ "$TOTAL_LINES" -gt 200 ]; then
      SYNCED_LINES=$((TOTAL_LINES - 200))
    fi

    if [ "$TOTAL_LINES" -gt "$SYNCED_LINES" ] 2>/dev/null; then
      # Extract project name from transcript path
      PROJECT=$(python3 -c "
tp = '$TRANSCRIPT_PATH'
parts = tp.split('/projects/')
if len(parts) > 1:
    d = parts[1].split('/')[0]
    if 'github_projects-' in d: print(d.split('github_projects-')[-1])
    elif d.startswith('-Users-'): print('home')
    else: print(d)
else: print('')
" 2>/dev/null || echo "")

      # Ensure session exists in cloud
      curl -sf -X POST "$BMFOTE_URL/api/sessions" \
        -H "$AUTH" -H "Content-Type: application/json" \
        -d "{\"session_id\":\"$SESSION_ID\",\"project\":\"$PROJECT\"}" > /dev/null 2>&1

      # Read new lines and POST each message
      tail -n +"$((SYNCED_LINES + 1))" "$TRANSCRIPT_PATH" | python3 -c "
import sys, json, ast, urllib.request, os

api = os.environ['BMFOTE_URL']
token = os.environ['BMFOTE_TOKEN']
session_id = os.environ['BMFOTE_SESSION_ID']

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except:
        continue
    msg_type = d.get('type', '')
    if msg_type not in ('user', 'assistant'):
        continue
    uuid = d.get('uuid', '')
    if not uuid:
        continue

    # Extract text content from message
    msg = d.get('message', {})
    if isinstance(msg, str):
        try: msg = ast.literal_eval(msg)
        except: msg = {}
    content_raw = msg.get('content', '')
    text = ''
    if isinstance(content_raw, list):
        for block in content_raw:
            if isinstance(block, dict) and block.get('type') == 'text':
                text += block.get('text', '') + '\n'
    elif isinstance(content_raw, str):
        text = content_raw
    if not text.strip():
        continue

    role = msg.get('role', msg_type)
    model = msg.get('model', d.get('model', ''))
    parent = d.get('parentUuid') or None
    timestamp = d.get('timestamp', '')

    payload = json.dumps({
        'session_id': session_id,
        'uuid': uuid,
        'type': msg_type,
        'role': role,
        'content': text[:10000],
        'model': model or None,
        'parent_uuid': parent,
        'timestamp': timestamp,
    }).encode()

    req = urllib.request.Request(
        f'{api}/api/messages',
        data=payload,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except:
        pass
" 2>/dev/null

      # Update sync marker
      echo "$TOTAL_LINES" > "$SYNC_MARKER"
    fi
  ) &
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
      "$BMFOTE_URL/api/recent?hours=8&limit=40&session_id=$SESSION_ID" 2>/dev/null)

    if [ -z "$RECENT" ] || [ "$RECENT" = "[]" ]; then
      RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
        "$BMFOTE_URL/api/recent?hours=8&limit=40" 2>/dev/null)
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

SESSIONS=$(curl -s --connect-timeout 2 --max-time 3 -H "$AUTH" \
  "$BMFOTE_URL/api/vault/list?project=$PROJECT&doc_type=session&limit=3" 2>/dev/null)

SESSION_CONTEXT=$(echo "$SESSIONS" | python3 -c "
import sys, json
sessions = json.load(sys.stdin)
if sessions:
    label = sessions[0].get('project', 'recent')
    lines = [f'Last {label} sessions:']
    for s in sessions:
        lines.append(f'  [{s[\"date\"]}] {s[\"topic\"]} ({s[\"outcome\"]})')
    print('\n'.join(lines))
" 2>/dev/null)

echo "Memory database available. Search with: curl -s -H 'Authorization: Bearer \$BMFOTE_TOKEN' '\$BMFOTE_URL/api/search?q=QUERY' | Read full message: curl -s -H 'Authorization: Bearer \$BMFOTE_TOKEN' '\$BMFOTE_URL/api/message/UUID?context=1'"
if [ -n "$SESSION_CONTEXT" ]; then
  echo "$SESSION_CONTEXT"
fi
