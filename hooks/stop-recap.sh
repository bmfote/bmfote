#!/bin/bash
# cctx stop-recap: generates a 30-word wry-engineer recap of the just-ended
# session and writes it to ~/.claude/cctx-recaps/<workspace>.txt.
#
# Background-spawned by cctx-stop.sh so session-end stays snappy. Every error
# path is silent — recap generation must never block a clean session exit.
#
# Invocation:
#   cctx-stop-recap.sh <session_id> <transcript_path>

set -u

SESSION_ID="${1:-}"
TRANSCRIPT_PATH="${2:-}"

[ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0

# Locate `claude` binary — silent exit if not available.
CLAUDE_BIN=$(command -v claude 2>/dev/null || true)
[ -z "$CLAUDE_BIN" ] && exit 0

# --- Resolve workspace ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
RESOLVER="$SCRIPT_DIR/cctx-lib/resolve-workspace.sh"
[ ! -f "$RESOLVER" ] && RESOLVER="$SCRIPT_DIR/lib/resolve-workspace.sh"

WORKSPACE_ID="${CCTX_WORKSPACE:-}"
if [ -z "$WORKSPACE_ID" ] && [ -f "$RESOLVER" ]; then
  # shellcheck source=/dev/null
  . "$RESOLVER"
  FAKE_INPUT=$(python3 -c "import json,sys; print(json.dumps({'transcript_path': sys.argv[1]}))" "$TRANSCRIPT_PATH" 2>/dev/null || echo '{}')
  resolve_workspace "$FAKE_INPUT"
fi
[ -z "$WORKSPACE_ID" ] && WORKSPACE_ID="cctx-default"

RECAP_DIR="$HOME/.claude/cctx-recaps"
mkdir -p "$RECAP_DIR" 2>/dev/null || exit 0
RECAP_FILE="$RECAP_DIR/$WORKSPACE_ID.txt"
RECAP_TMP="$RECAP_FILE.tmp.$$"

# --- Extract last ~30 user/assistant text turns into a plain transcript ---
TRANSCRIPT_TEXT=$(TRANSCRIPT_PATH="$TRANSCRIPT_PATH" python3 <<'PY' 2>/dev/null
import json, os, sys
path = os.environ.get('TRANSCRIPT_PATH', '')
rows = []
try:
    with open(path) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get('type')
            if t not in ('user', 'assistant'): continue
            msg = d.get('message') or {}
            content = msg.get('content')
            text = ''
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'text':
                        parts.append(c.get('text', ''))
                text = '\n'.join(p for p in parts if p)
            text = (text or '').strip()
            if not text: continue
            if text.startswith('<system-reminder>') or text.startswith('<command-name>'):
                continue
            rows.append((t, text[:500]))
except Exception:
    sys.exit(0)

for role, txt in rows[-30:]:
    print(f"[{role}] {txt}\n")
PY
)

[ -z "$TRANSCRIPT_TEXT" ] && exit 0

# --- Ask claude -p for the recap ---
SYSTEM_PROMPT='Recap this Claude Code session in ONE present-tense sentence, 30 words max. Voice: a wry, literate senior engineer — dry wit, deadpan delivery, classy never crass. Mock the bug, the tooling, the universe; never the human. The joke lives in CONCRETE specifics: name the file, the line, the absurd variable, the exact dumb thing that broke. Vague = unfunny. Forbidden: the words "successfully", "just", "navigate", "leverage"; emoji; preambles ("Here is..."); quotes; markdown; hedging; trailing summary clauses ("...and moves on", "...and lives to debug another day"). Land the joke and stop. One line, no signoff.'

USER_INPUT=$(printf 'System: %s\n\nTranscript:\n%s\n\nRecap:' "$SYSTEM_PROMPT" "$TRANSCRIPT_TEXT")

# 45s cap; if claude -p hangs or fails, we silently abort.
RECAP=$(printf '%s' "$USER_INPUT" | CCTX_SKIP_HOOKS=1 "$CLAUDE_BIN" -p --model haiku 2>/dev/null) || exit 0
[ -z "$RECAP" ] && exit 0

# --- Sanitize: strip ANSI, collapse whitespace, single line, 30-word cap ---
RECAP=$(printf '%s' "$RECAP" | python3 -c "
import sys, re
s = sys.stdin.read()
s = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', s)   # strip ANSI
s = s.strip().strip('\"').strip(\"'\")
s = re.sub(r'\s+', ' ', s)
words = s.split(' ')
if len(words) > 30:
    s = ' '.join(words[:30]).rstrip(',.;:') + '…'
print(s)
" 2>/dev/null)

[ -z "$RECAP" ] && exit 0

# --- Atomic write ---
printf '%s\n' "$RECAP" > "$RECAP_TMP" 2>/dev/null || exit 0
mv -f "$RECAP_TMP" "$RECAP_FILE" 2>/dev/null || { rm -f "$RECAP_TMP"; exit 0; }

exit 0
