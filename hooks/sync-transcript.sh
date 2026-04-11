#!/bin/bash
# Shared transcript sync logic for bmfote hooks.
# Called by both UserPromptSubmit (background) and Stop (foreground).
#
# Reads the local JSONL transcript and POSTs new messages to the cloud API.
# Uses marker files for incremental sync — only new lines since last sync.
#
# Required env vars: BMFOTE_URL, BMFOTE_TOKEN
# Arguments: $1 = session_id, $2 = transcript_path

set +e +o pipefail  # don't abort on individual failures

BMFOTE_URL="${BMFOTE_URL:-}"
BMFOTE_TOKEN="${BMFOTE_TOKEN:-}"
SESSION_ID="${1:-}"
TRANSCRIPT_PATH="${2:-}"

if [ -z "$BMFOTE_URL" ] || [ -z "$BMFOTE_TOKEN" ] || [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi
if [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

AUTH="Authorization: Bearer $BMFOTE_TOKEN"
SYNC_MARKER_DIR="$HOME/.claude/hooks/.sync-markers"
mkdir -p "$SYNC_MARKER_DIR"

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

# Nothing new to sync
if [ "$TOTAL_LINES" -le "$SYNCED_LINES" ] 2>/dev/null; then
  exit 0
fi

# Extract project name from transcript path
PROJECT=$(python3 -c "
import sys
tp = sys.argv[1]
parts = tp.split('/projects/')
if len(parts) > 1:
    d = parts[1].split('/')[0]
    if 'github_projects-' in d: print(d.split('github_projects-')[-1])
    elif d.startswith('-Users-'): print('home')
    else: print(d)
else: print('')
" "$TRANSCRIPT_PATH" 2>/dev/null || echo "")

# Ensure session exists in cloud
curl -sf -X POST "$BMFOTE_URL/api/sessions" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SESSION_ID\",\"project\":\"$PROJECT\"}" > /dev/null 2>&1

# Read new lines and POST each message
export BMFOTE_URL BMFOTE_TOKEN
export BMFOTE_SESSION_ID="$SESSION_ID"

tail -n +"$((SYNCED_LINES + 1))" "$TRANSCRIPT_PATH" | python3 -c "
import sys, json, urllib.request, os

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
        try: msg = json.loads(msg)
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
