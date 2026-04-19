#!/bin/bash
# cctx stop-definitions: at session end, asks Haiku whether any tracked
# canonical definition file should be updated based on the conversation,
# then POSTs each proposal (confidence >= 0.7) to the review queue for
# human review via `cctx review`.
#
# Local files remain source-of-truth. This hook only records AI-proposed
# edits — the CLI applies approved proposals to disk.
#
# Invocation (background-spawned by cctx-stop.sh):
#   cctx-stop-definitions.sh <session_id> <transcript_path> <cwd>
#
# Silent on all error paths — must never block session exit.

set -u

SESSION_ID="${1:-}"
TRANSCRIPT_PATH="${2:-}"
CWD="${3:-}"

[ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ] || [ -z "$CWD" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0
[ ! -d "$CWD" ] && exit 0

# --- Tracked-file manifest ---
TRACKED_MANIFEST="$CWD/.cctx/tracked.txt"
[ ! -f "$TRACKED_MANIFEST" ] && exit 0

# Read manifest — skip blank lines and comments
TRACKED_FILES=$(grep -vE '^\s*(#|$)' "$TRACKED_MANIFEST" 2>/dev/null || true)
[ -z "$TRACKED_FILES" ] && exit 0

# --- Locate claude binary ---
CLAUDE_BIN=$(command -v claude 2>/dev/null || true)
[ -z "$CLAUDE_BIN" ] && exit 0

# --- Load CCTX_URL / CCTX_TOKEN ---
CCTX_CONFIG="$HOME/.claude/cctx.env"
if [ -f "$CCTX_CONFIG" ]; then
  . "$CCTX_CONFIG"
fi
CCTX_URL="${CCTX_URL:-}"
CCTX_TOKEN="${CCTX_TOKEN:-}"
[ -z "$CCTX_URL" ] || [ -z "$CCTX_TOKEN" ] && exit 0

# --- Resolve workspace (same pattern as stop-recap.sh) ---
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

# --- Calibration log (append-only for prompt tuning) ---
CAL_DIR="$HOME/.claude/cctx-definitions"
mkdir -p "$CAL_DIR" 2>/dev/null || exit 0
CAL_LOG="$CAL_DIR/$WORKSPACE_ID.jsonl"

# --- Canonical system prompt (kept in sync with docs/definitions-prompt.md) ---
read -r -d '' SYSTEM_PROMPT <<'PROMPT' || true
You are reviewing the last ~30 turns of a Claude Code session to decide whether any tracked *canonical project definition* should be updated. The user maintains small markdown files (icp.md, playbook.md, pricing.md, infrastructure.md) as living truth-documents.

## Primary directive: bias toward silence

The cost of a bad proposal (user stops trusting this feature) vastly exceeds the cost of a missed proposal (they manually edit later). When in doubt, propose nothing. If the session was exploratory, speculative, or tangential, return an empty array.

## What qualifies for a proposal

ALL four must be true:
1. Concreteness: the session contains a specific, non-speculative statement about the definition.
2. Relevance: the statement pertains to content already in the tracked file.
3. Surgical scope: the edit is the smallest change capturing the new info (1-3 sentences, one paragraph, or one bullet).
4. Defensible reasoning: you can point to a specific transcript phrase justifying the edit.

## What disqualifies

- Implicit, hedged, or might-be information
- Debugging, coding, or writing copy (not definition decisions)
- Topic mentioned but nothing decided
- Rephrasing existing content without new meaning
- "Tidy up" impulses

## Output format

Return a strict JSON array. NO prose, NO preamble, NO code fences.

[
  {"file": "icp.md", "old": "<exact current paragraph to replace>", "new": "<surgical replacement>", "reason": "<one sentence citing a transcript statement>", "confidence": 0.0-1.0}
]

Multiple edits across different files allowed. Return [] if nothing meets the four criteria. Empty is the most common correct output.

## Confidence calibration

- 0.9-1.0: explicit unambiguous decision
- 0.7-0.89: clear refinement with cited transcript statement
- 0.5-0.69: plausible but ambiguous (still return; caller filters)
- <0.5: guessing — return [] instead

## Safety rails

- `old` must match the file byte-exactly. If you can't find an exact match, propose nothing for that file.
- `new` preserves surrounding formatting.
- Never propose deletions (empty `new`).
- First-write to empty file: `old: ""`, confidence must be >=0.8.
PROMPT

# --- Build the user turn: current file contents + transcript tail ---
USER_TURN_FILE=$(mktemp -t cctx-def-user.XXXXXX) || exit 0
trap 'rm -f "$USER_TURN_FILE"' EXIT

{
  printf '## Tracked definition files (current contents)\n\n'
  while IFS= read -r rel_path; do
    [ -z "$rel_path" ] && continue
    full_path="$CWD/$rel_path"
    if [ -f "$full_path" ]; then
      size=$(wc -c < "$full_path" | tr -d ' ')
      # Cap each file at 40KB to keep prompt tight
      if [ "$size" -gt 40960 ]; then
        printf '### %s (truncated, %s bytes)\n\n```\n' "$rel_path" "$size"
        head -c 40960 "$full_path"
        printf '\n```\n\n'
      else
        printf '### %s\n\n```\n' "$rel_path"
        cat "$full_path"
        printf '\n```\n\n'
      fi
    else
      printf '### %s (file does not exist yet)\n\n```\n```\n\n' "$rel_path"
    fi
  done <<< "$TRACKED_FILES"

  printf '## Last turns of the session\n\n'

  TRANSCRIPT_PATH="$TRANSCRIPT_PATH" python3 <<'PY' 2>/dev/null
import json, os, sys
path = os.environ.get('TRANSCRIPT_PATH', '')
rows = []
try:
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get('type')
            if t not in ('user', 'assistant'):
                continue
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
            if not text:
                continue
            if text.startswith('<system-reminder>') or text.startswith('<command-name>'):
                continue
            rows.append((t, text[:2000]))
except Exception:
    sys.exit(0)

for role, txt in rows[-30:]:
    print(f"[{role}] {txt}\n")
PY

  printf '\n## Your response\n\nReturn the JSON array. Nothing else.\n'
} > "$USER_TURN_FILE"

# If nothing useful made it into the user turn, abort
[ ! -s "$USER_TURN_FILE" ] && exit 0

# --- Call Haiku ---
COMBINED_INPUT=$(printf 'System:\n%s\n\n---\n\n' "$SYSTEM_PROMPT"; cat "$USER_TURN_FILE")
RAW=$(printf '%s' "$COMBINED_INPUT" | CCTX_SKIP_HOOKS=1 "$CLAUDE_BIN" -p --model haiku 2>/dev/null) || exit 0
[ -z "$RAW" ] && exit 0

# --- Parse + validate + POST ---
RAW_B64=$(printf '%s' "$RAW" | base64 2>/dev/null)
[ -z "$RAW_B64" ] && exit 0

CCTX_URL="$CCTX_URL" \
CCTX_TOKEN="$CCTX_TOKEN" \
SESSION_ID="$SESSION_ID" \
WORKSPACE_ID="$WORKSPACE_ID" \
CWD="$CWD" \
CAL_LOG="$CAL_LOG" \
RAW_B64="$RAW_B64" \
python3 <<'PY' 2>/dev/null
import base64, json, os, re, sys, time, urllib.request, urllib.error, uuid

raw = base64.b64decode(os.environ['RAW_B64']).decode('utf-8', errors='replace')
workspace = os.environ['WORKSPACE_ID']
session_id = os.environ['SESSION_ID']
cwd = os.environ['CWD']
cctx_url = os.environ['CCTX_URL'].rstrip('/')
cctx_token = os.environ['CCTX_TOKEN']
cal_log = os.environ['CAL_LOG']

# Haiku sometimes wraps JSON in code fences. Strip them.
m = re.search(r'\[[\s\S]*\]', raw)
if not m:
    # Log the non-JSON response for calibration
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'status': 'no_json', 'raw_tail': raw[-400:],
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

try:
    proposals = json.loads(m.group(0))
except Exception as e:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'status': 'invalid_json', 'err': str(e)[:200],
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

if not isinstance(proposals, list) or not proposals:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'status': 'no_proposals',
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

submitted = 0
for p in proposals:
    if not isinstance(p, dict):
        continue
    file_path = (p.get('file') or '').strip()
    new_content = p.get('new') or ''
    old_content = p.get('old') or ''
    reason = (p.get('reason') or '').strip() or None
    try:
        confidence = float(p.get('confidence', 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    log_base = {
        'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
        'file': file_path, 'confidence': confidence, 'reason': (reason or '')[:160],
    }

    if not file_path or not new_content:
        log_base['status'] = 'skipped_missing_fields'
        try:
            with open(cal_log, 'a') as f:
                f.write(json.dumps(log_base) + '\n')
        except Exception:
            pass
        continue

    # Confidence floor 0.7 — per prompt spec
    if confidence < 0.7:
        log_base['status'] = 'filtered_low_confidence'
        try:
            with open(cal_log, 'a') as f:
                f.write(json.dumps(log_base) + '\n')
        except Exception:
            pass
        continue

    # Validate `old` matches current file content byte-exactly
    full_path = os.path.join(cwd, file_path)
    current = ''
    if os.path.isfile(full_path):
        try:
            with open(full_path) as f:
                current = f.read()
        except Exception:
            current = ''

    if old_content:
        if old_content not in current:
            log_base['status'] = 'rejected_old_mismatch'
            try:
                with open(cal_log, 'a') as f:
                    f.write(json.dumps(log_base) + '\n')
            except Exception:
                pass
            continue
    else:
        # First-write: file must be empty or not exist
        if current.strip():
            log_base['status'] = 'rejected_first_write_but_file_nonempty'
            try:
                with open(cal_log, 'a') as f:
                    f.write(json.dumps(log_base) + '\n')
            except Exception:
                pass
            continue

    # POST to cctx
    edit_uuid = str(uuid.uuid4())
    payload = {
        'uuid': edit_uuid,
        'workspace_id': workspace,
        'file_path': file_path,
        'new_content': new_content,
        'old_content': old_content or None,
        'reason': reason,
        'confidence': confidence,
        'source_session_id': session_id,
    }

    try:
        req = urllib.request.Request(
            f'{cctx_url}/api/definitions/propose',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {cctx_token}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        submitted += 1
        log_base['status'] = 'submitted'
        log_base['uuid'] = edit_uuid
    except Exception as e:
        log_base['status'] = 'post_failed'
        log_base['err'] = str(e)[:200]

    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps(log_base) + '\n')
    except Exception:
        pass
PY

exit 0
