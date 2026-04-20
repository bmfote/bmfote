#!/bin/bash
# cctx stop-definitions v2: observer + catch-net model
#
# For each tracked definition file:
#   1. OBSERVER — if the file changed during the session, classify the change
#      (minor / pivot / graveyard) and update the .def provenance file.
#   2. CATCH-NET — if the file did NOT change, run the original proposer to
#      detect decisions discussed but not yet applied to the file.
#
# .def files live at $CWD/.cctx/definitions/<filename>.def and capture:
#   - Now: compressed summary of current file state
#   - Pivots: significant direction changes with session provenance
#   - Graveyard: explicitly abandoned approaches
#
# Invocation (background-spawned by cctx-stop.sh):
#   stop-definitions.sh <session_id> <transcript_path> <cwd>
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

# --- Calibration log ---
CAL_DIR="$HOME/.claude/cctx-definitions"
mkdir -p "$CAL_DIR" 2>/dev/null || exit 0
CAL_LOG="$CAL_DIR/$WORKSPACE_ID.jsonl"

# --- Snapshot directory (created by UserPromptSubmit hook) ---
SNAPSHOT_DIR="$HOME/.claude/hooks/.def-snapshots/$SESSION_ID"

# --- Definitions output directory ---
DEF_DIR="$CWD/.cctx/definitions"
mkdir -p "$DEF_DIR" 2>/dev/null || exit 0

# --- Extract transcript tail once (shared by observer + catch-net) ---
TRANSCRIPT_TAIL=$(TRANSCRIPT_PATH="$TRANSCRIPT_PATH" python3 <<'PY' 2>/dev/null || true
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

for role, txt in rows[-20:]:
    print(f"[{role}] {txt}\n")
PY
)
[ -z "$TRANSCRIPT_TAIL" ] && exit 0

# --- Temp files ---
OBSERVER_INPUT=$(mktemp -t cctx-obs.XXXXXX) || exit 0
CATCHNET_INPUT=$(mktemp -t cctx-catch.XXXXXX) || exit 0
trap 'rm -f "$OBSERVER_INPUT" "$CATCHNET_INPUT"' EXIT

# =====================================================================
# PHASE 1: OBSERVER — classify changes to files that were modified
# =====================================================================

CHANGED_FILES=""
UNCHANGED_FILES=""

while IFS= read -r rel_path; do
  [ -z "$rel_path" ] && continue
  snapshot_file="$SNAPSHOT_DIR/$rel_path"
  current_file="$CWD/$rel_path"

  if [ -f "$snapshot_file" ] && [ -f "$current_file" ]; then
    if ! diff -q "$snapshot_file" "$current_file" > /dev/null 2>&1; then
      CHANGED_FILES="${CHANGED_FILES}${rel_path}\n"
    else
      UNCHANGED_FILES="${UNCHANGED_FILES}${rel_path}\n"
    fi
  else
    UNCHANGED_FILES="${UNCHANGED_FILES}${rel_path}\n"
  fi
done <<< "$TRACKED_FILES"

# --- Run observer for each changed file ---
if [ -n "$CHANGED_FILES" ]; then
  printf '%b' "$CHANGED_FILES" | while IFS= read -r rel_path; do
    [ -z "$rel_path" ] && continue
    snapshot_file="$SNAPSHOT_DIR/$rel_path"
    current_file="$CWD/$rel_path"
    def_base="${rel_path%.*}"
    def_file="$DEF_DIR/${def_base}.def"

    FILE_DIFF=$(diff -u "$snapshot_file" "$current_file" 2>/dev/null || true)
    [ -z "$FILE_DIFF" ] && continue

    CURRENT_CONTENT=$(cat "$current_file" 2>/dev/null || true)
    EXISTING_DEF=""
    [ -f "$def_file" ] && EXISTING_DEF=$(cat "$def_file" 2>/dev/null || true)

    # Build observer prompt
    {
      printf 'System:\nYou are classifying a change made to a tracked project definition file during a Claude Code session.\n\n'
      printf '## Instructions\n\n'
      printf 'You will receive: (1) the diff of what changed, (2) the current file contents, (3) recent transcript turns.\n\n'
      printf 'Classify the change into ONE of:\n'
      printf '- "minor": Core direction unchanged. Rewording, adding detail, fixing formatting, adding examples.\n'
      printf '- "pivot": Significant direction change. New target audience, abandoned strategy replaced, fundamental shift.\n'
      printf '- "graveyard_add": Something was explicitly removed or abandoned.\n\n'
      printf 'DEFAULT TO "minor". Pivots require genuine directional change, not just refinement.\n\n'
      printf 'Return strict JSON. NO prose, NO code fences.\n\n'
      printf '{"classification": "minor|pivot|graveyard_add", "now_summary": "<5-7 line summary of the file current state — what it IS now, compressed>", "pivot_description": "<if pivot: one sentence describing the direction change, else empty string>", "pivot_reason": "<if pivot: why, citing transcript, else empty string>", "graveyard_items": ["<if graveyard_add: what was removed/abandoned — reason>"], "confidence": 0.0-1.0}\n\n'
      printf '---\n\n'
      printf '## File: %s\n\n' "$rel_path"
      printf '### Diff\n```\n%s\n```\n\n' "$FILE_DIFF"
      printf '### Current file contents\n```\n%s\n```\n\n' "$CURRENT_CONTENT"
      printf '### Recent session transcript\n\n%s\n\n' "$TRANSCRIPT_TAIL"
      printf '## Your response\n\nReturn the JSON object. Nothing else.\n'
    } > "$OBSERVER_INPUT"

    RAW=$(cat "$OBSERVER_INPUT" | CCTX_SKIP_HOOKS=1 "$CLAUDE_BIN" -p --model haiku 2>/dev/null) || continue
    [ -z "$RAW" ] && continue

    # Parse observer response and update .def file
    RAW_B64=$(printf '%s' "$RAW" | base64 2>/dev/null)
    [ -z "$RAW_B64" ] && continue

    RAW_B64="$RAW_B64" \
    EXISTING_DEF_B64=$(printf '%s' "$EXISTING_DEF" | base64 2>/dev/null) \
    REL_PATH="$rel_path" \
    DEF_FILE="$def_file" \
    SESSION_ID="$SESSION_ID" \
    WORKSPACE_ID="$WORKSPACE_ID" \
    CCTX_URL="$CCTX_URL" \
    CCTX_TOKEN="$CCTX_TOKEN" \
    CAL_LOG="$CAL_LOG" \
    python3 <<'PY' 2>/dev/null
import base64, json, os, re, sys, time, urllib.request
from datetime import date

raw = base64.b64decode(os.environ['RAW_B64']).decode('utf-8', errors='replace')
existing_def = base64.b64decode(os.environ.get('EXISTING_DEF_B64', '')).decode('utf-8', errors='replace') if os.environ.get('EXISTING_DEF_B64') else ''
rel_path = os.environ['REL_PATH']
def_file = os.environ['DEF_FILE']
session_id = os.environ['SESSION_ID']
workspace = os.environ['WORKSPACE_ID']
cal_log = os.environ['CAL_LOG']
today = date.today().isoformat()
short_session = session_id[:8]

# Parse Haiku response
m = re.search(r'\{[\s\S]*\}', raw)
if not m:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'file': rel_path, 'mode': 'observer', 'status': 'no_json',
                'raw_tail': raw[-400:],
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

try:
    result = json.loads(m.group(0))
except Exception as e:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'file': rel_path, 'mode': 'observer', 'status': 'invalid_json',
                'err': str(e)[:200],
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

classification = result.get('classification', 'minor')
now_summary = (result.get('now_summary') or '').strip()
pivot_desc = (result.get('pivot_description') or '').strip()
pivot_reason = (result.get('pivot_reason') or '').strip()
graveyard_items = result.get('graveyard_items') or []
confidence = float(result.get('confidence', 0.5))

if not now_summary:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'file': rel_path, 'mode': 'observer', 'status': 'empty_summary',
                'classification': classification,
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

# --- Parse existing .def file ---
version = 0
pivots = []
graveyard = []

if existing_def:
    # Extract version from frontmatter
    fm_match = re.search(r'^---\s*\n(.*?)\n---', existing_def, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split('\n'):
            if line.startswith('version:'):
                try:
                    version = int(line.split(':', 1)[1].strip())
                except (ValueError, IndexError):
                    pass

    # Extract pivots
    pivots_match = re.search(r'## Pivots\n(.*?)(?=\n## |\Z)', existing_def, re.DOTALL)
    if pivots_match:
        for line in pivots_match.group(1).strip().split('\n'):
            line = line.strip()
            if line.startswith('- **'):
                pivots.append(line)

    # Extract graveyard
    gy_match = re.search(r'## Graveyard\n(.*?)(?=\n## |\Z)', existing_def, re.DOTALL)
    if gy_match:
        for line in gy_match.group(1).strip().split('\n'):
            line = line.strip()
            if line.startswith('- '):
                graveyard.append(line)

# --- Update based on classification ---
new_version = version + 1

if classification == 'pivot' and pivot_desc and confidence >= 0.7:
    pivot_entry = f'- **v{version}->v{new_version}** ({today}, session:{short_session}) -- {pivot_desc} *Why: {pivot_reason}*'
    pivots.insert(0, pivot_entry)
    if len(pivots) > 5:
        pivots = pivots[:5]

if classification == 'graveyard_add':
    for item in graveyard_items:
        if isinstance(item, str) and item.strip():
            gy_entry = f'- {item.strip()} ({today})'
            graveyard.append(gy_entry)
    if len(graveyard) > 10:
        graveyard = graveyard[-10:]

# --- Build .def file ---
pivots_text = '\n'.join(pivots) if pivots else '(none yet)'
graveyard_text = '\n'.join(graveyard) if graveyard else '(none yet)'

def_content = f"""---
tracks: {rel_path}
version: {new_version}
updated: {today}
session: {short_session}
---

## Now
{now_summary}

## Pivots
{pivots_text}

## Graveyard
{graveyard_text}

## History
Full provenance: `cctx search "def:{rel_path}" --workspace {workspace}`
"""

# Write .def file
os.makedirs(os.path.dirname(def_file), exist_ok=True)
with open(def_file, 'w') as f:
    f.write(def_content)

# Push .def content to database for team sync (fail-open)
cctx_url = os.environ.get('CCTX_URL', '').rstrip('/')
cctx_token = os.environ.get('CCTX_TOKEN', '')
if cctx_url and cctx_token:
    try:
        payload = json.dumps({
            'workspace_id': workspace,
            'file_path': rel_path,
            'content': def_content,
            'version': new_version,
            'session_id': session_id,
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{cctx_url}/api/def-files',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {cctx_token}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        pass

# Log success
try:
    with open(cal_log, 'a') as f:
        f.write(json.dumps({
            'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
            'file': rel_path, 'mode': 'observer', 'status': 'def_updated',
            'classification': classification, 'version': new_version,
            'confidence': confidence,
        }) + '\n')
except Exception:
    pass
PY
  done
fi

# =====================================================================
# PHASE 2: CATCH-NET — propose edits for files that were NOT modified
# =====================================================================

# Only run if there are unchanged files
if [ -z "$UNCHANGED_FILES" ]; then
  # Clean up snapshots
  rm -rf "$SNAPSHOT_DIR" 2>/dev/null
  exit 0
fi

# --- Catch-net system prompt (original proposer, bias toward silence) ---
read -r -d '' CATCHNET_PROMPT <<'PROMPT' || true
You are reviewing the last ~20 turns of a Claude Code session to decide whether any tracked *canonical project definition* should be updated. The user maintains small markdown files as living truth-documents.

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

Return [] if nothing meets the four criteria. Empty is the most common correct output.

## Confidence calibration

- 0.9-1.0: explicit unambiguous decision
- 0.7-0.89: clear refinement with cited transcript statement
- <0.7: do not propose

## Safety rails

- `old` must match the file byte-exactly. If you can't find an exact match, propose nothing.
- `new` preserves surrounding formatting.
- Never propose deletions (empty `new`).
- First-write to empty file: `old: ""`, confidence must be >=0.8.
PROMPT

# Build user turn for catch-net (only unchanged files)
{
  printf '## Tracked definition files (current contents)\n\n'
  printf '%b' "$UNCHANGED_FILES" | while IFS= read -r rel_path; do
    [ -z "$rel_path" ] && continue
    full_path="$CWD/$rel_path"
    if [ -f "$full_path" ]; then
      size=$(wc -c < "$full_path" | tr -d ' ')
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
  done

  printf '## Last turns of the session\n\n'
  printf '%s\n' "$TRANSCRIPT_TAIL"
  printf '\n## Your response\n\nReturn the JSON array. Nothing else.\n'
} > "$CATCHNET_INPUT"

[ ! -s "$CATCHNET_INPUT" ] && { rm -rf "$SNAPSHOT_DIR" 2>/dev/null; exit 0; }

# Call Haiku for catch-net
COMBINED_INPUT=$(printf 'System:\n%s\n\n---\n\n' "$CATCHNET_PROMPT"; cat "$CATCHNET_INPUT")
RAW=$(printf '%s' "$COMBINED_INPUT" | CCTX_SKIP_HOOKS=1 "$CLAUDE_BIN" -p --model haiku 2>/dev/null) || { rm -rf "$SNAPSHOT_DIR" 2>/dev/null; exit 0; }
[ -z "$RAW" ] && { rm -rf "$SNAPSHOT_DIR" 2>/dev/null; exit 0; }

# Parse + validate + POST (same as original proposer)
RAW_B64=$(printf '%s' "$RAW" | base64 2>/dev/null)
[ -z "$RAW_B64" ] && { rm -rf "$SNAPSHOT_DIR" 2>/dev/null; exit 0; }

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

m = re.search(r'\[[\s\S]*\]', raw)
if not m:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'mode': 'catchnet', 'status': 'no_json', 'raw_tail': raw[-400:],
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
                'mode': 'catchnet', 'status': 'invalid_json', 'err': str(e)[:200],
            }) + '\n')
    except Exception:
        pass
    sys.exit(0)

if not isinstance(proposals, list) or not proposals:
    try:
        with open(cal_log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'session_id': session_id, 'workspace': workspace,
                'mode': 'catchnet', 'status': 'no_proposals',
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
        'mode': 'catchnet',
    }

    if not file_path or not new_content:
        log_base['status'] = 'skipped_missing_fields'
        try:
            with open(cal_log, 'a') as f:
                f.write(json.dumps(log_base) + '\n')
        except Exception:
            pass
        continue

    if confidence < 0.7:
        log_base['status'] = 'filtered_low_confidence'
        try:
            with open(cal_log, 'a') as f:
                f.write(json.dumps(log_base) + '\n')
        except Exception:
            pass
        continue

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
        if current.strip():
            log_base['status'] = 'rejected_first_write_but_file_nonempty'
            try:
                with open(cal_log, 'a') as f:
                    f.write(json.dumps(log_base) + '\n')
            except Exception:
                pass
            continue

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

# --- Clean up snapshots ---
rm -rf "$SNAPSHOT_DIR" 2>/dev/null

exit 0
