#!/bin/bash
# Cloud-native PreCompact hook for bmfote.
# Feeds recent messages to the compaction summarizer as additionalContext
# so the summary retains recent work details.
#
# No local database required — talks directly to the Railway API.
# Requires: BMFOTE_URL and BMFOTE_TOKEN env vars (set by bmfote installer)

BMFOTE_URL="${BMFOTE_URL:-}"
BMFOTE_TOKEN="${BMFOTE_TOKEN:-}"

if [ -z "$BMFOTE_URL" ] || [ -z "$BMFOTE_TOKEN" ]; then
  exit 0
fi

AUTH="Authorization: Bearer $BMFOTE_TOKEN"

# Read session info from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)

# Fetch recent messages from cloud API
RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
  "$BMFOTE_URL/api/recent?hours=6&limit=30&session_id=$SESSION_ID" 2>/dev/null)

if [ -z "$RECENT" ] || [ "$RECENT" = "[]" ]; then
  RECENT=$(curl -s --connect-timeout 2 --max-time 5 -H "$AUTH" \
    "$BMFOTE_URL/api/recent?hours=6&limit=30" 2>/dev/null)
fi

if [ -z "$RECENT" ] || [ "$RECENT" = "[]" ]; then
  exit 0
fi

# Format messages for the summarizer (oldest first, truncated)
FORMATTED=$(echo "$RECENT" | python3 -c "
import sys, json
messages = json.load(sys.stdin)
messages.reverse()
lines = []
for m in messages:
    role = m.get('role', m.get('type', '?'))
    content = (m.get('content') or '')[:800]
    ts = m.get('timestamp', '')[:19]
    lines.append(f'[{ts}] {role}: {content}')
print('\n---\n'.join(lines))
" 2>/dev/null)

if [ -n "$FORMATTED" ]; then
  python3 -c "
import json
ctx = '''IMPORTANT - Recent conversation messages from the memory database. These are the actual messages from this session that should be preserved in the summary:

$FORMATTED'''
print(json.dumps({'additionalContext': ctx}))
"
fi
