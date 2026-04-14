#!/bin/bash
# bmfote setup — configure any machine for cloud memory in one command.
#
# Usage:
#   npx bmfote setup
#
# Or curl one-liner:
#   curl -fsSL https://raw.githubusercontent.com/bmfote/bmfote/main/installer/setup.sh | bash
#
# What it does:
#   1. Verifies Claude Code is installed
#   2. Tests connection to the bmfote API
#   3. Adds MCP server to Claude Code (user scope)
#   4. Downloads/copies hook scripts to ~/.claude/hooks/
#   5. Configures hooks in ~/.claude/settings.json
#   6. Sets BMFOTE_URL and BMFOTE_TOKEN in shell profile
#
# Safe to re-run — skips steps that are already configured.

set -euo pipefail

# --- Parse arguments (--url and --token are required) ---
BMFOTE_URL=""
BMFOTE_TOKEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)  BMFOTE_URL="$2"; shift 2 ;;
    --token) BMFOTE_TOKEN="$2"; shift 2 ;;
    setup) shift ;;  # allow "bmfote setup" — just skip the word
    -h|--help)
      echo "Usage: npx bmfote setup --url <API_URL> --token <API_TOKEN>"
      echo ""
      echo "Options (both required):"
      echo "  --url <url>      Your bmfote API URL (from 'npx bmfote deploy')"
      echo "  --token <token>  Your API token (from 'npx bmfote deploy')"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ -z "$BMFOTE_URL" ] || [ -z "$BMFOTE_TOKEN" ]; then
  echo "ERROR: --url and --token are required."
  echo ""
  echo "Usage: npx bmfote setup --url <API_URL> --token <API_TOKEN>"
  echo ""
  echo "See https://github.com/bmfote/bmfote#part-1-deploy-the-server to get your URL and token."
  exit 1
fi

# Strip trailing slash from URL
BMFOTE_URL="${BMFOTE_URL%/}"

echo "bmfote setup"
echo "============"
echo ""

# --- Step 1: Verify Claude Code ---
echo "[1/6] Checking Claude Code..."
if ! command -v claude &> /dev/null; then
  echo "  ERROR: Claude Code CLI not found."
  echo "  Install it: https://docs.anthropic.com/en/docs/claude-code/overview"
  exit 1
fi
CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
echo "  Found: claude $CLAUDE_VERSION"

# --- Step 2: Test API connection ---
echo "[2/6] Testing API connection..."
STATS=$(curl -sf --connect-timeout 5 --max-time 10 \
  -H "Authorization: Bearer $BMFOTE_TOKEN" \
  "$BMFOTE_URL/api/stats" 2>/dev/null) || {
  echo "  ERROR: Could not reach $BMFOTE_URL/api/stats"
  echo "  Check your --url and --token values."
  exit 1
}
MSG_COUNT=$(echo "$STATS" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d['messages'])
except (json.JSONDecodeError, KeyError, TypeError):
    sys.exit(1)
" 2>/dev/null) || {
  echo "  ERROR: API responded but didn't return valid stats JSON."
  echo "  Check your --url — a proxy or wrong host may be intercepting."
  exit 1
}
echo "  Connected: $MSG_COUNT messages in database"

# --- Step 3: Add MCP server ---
echo "[3/6] Configuring MCP server..."
# Check if already configured
EXISTING=$(claude mcp list 2>/dev/null | grep -c '^bmfote-memory:' || true)
if [ "$EXISTING" -gt 0 ]; then
  echo "  MCP server 'bmfote-memory' already configured — removing old entry"
  claude mcp remove -s user bmfote-memory 2>/dev/null || true
fi

claude mcp add -s user --transport http \
  bmfote-memory "$BMFOTE_URL/mcp/" \
  --header "Authorization: Bearer $BMFOTE_TOKEN"

echo "  Added MCP server: bmfote-memory (user scope)"

# --- Step 4: Install hook scripts ---
echo "[4/6] Installing hook scripts..."
HOOKS_DIR="$HOME/.claude/hooks"
mkdir -p "$HOOKS_DIR"

GITHUB_RAW="https://raw.githubusercontent.com/bmfote/bmfote/main/hooks"

# Try local repo first, fall back to GitHub download
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
HOOKS_SRC="${SCRIPT_DIR:+$SCRIPT_DIR/../hooks}"

for hook in post-compaction-context.sh pre-compaction-context.sh stop.sh sync-transcript.sh; do
  TARGET="$HOOKS_DIR/bmfote-$hook"
  if [ -f "$TARGET" ]; then
    echo "  Updating: $TARGET"
  else
    echo "  Installing: $TARGET"
  fi

  if [ -n "$HOOKS_SRC" ] && [ -f "$HOOKS_SRC/$hook" ]; then
    cp "$HOOKS_SRC/$hook" "$TARGET"
  else
    curl -fsSL "$GITHUB_RAW/$hook" -o "$TARGET" || {
      echo "  ERROR: Failed to download $hook from GitHub"
      exit 1
    }
  fi
  chmod +x "$TARGET"
done

# --- Step 5: Configure hooks in settings.json ---
echo "[5/6] Configuring hooks in settings.json..."
SETTINGS="$HOME/.claude/settings.json"

# Use python to safely merge hooks into existing settings
python3 << 'PYEOF'
import json, os, sys

settings_path = os.path.expanduser("~/.claude/settings.json")

# Read existing settings or start fresh
if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
else:
    settings = {}

hooks = settings.setdefault("hooks", {})
hooks_dir = os.path.expanduser("~/.claude/hooks")
changed = False

# Strip legacy non-prefixed entries left over from pre-0.5 installs.
# Older versions wrote these script names without the "bmfote-" prefix,
# so an upgrade would double-register the hook and fire it twice per turn.
LEGACY_SCRIPTS = ("post-compaction-context.sh", "pre-compaction-context.sh", "stop.sh")

def _is_legacy(cmd):
    if not cmd or "bmfote-" in cmd:
        return False
    return any(cmd.endswith("/" + s) or cmd == s for s in LEGACY_SCRIPTS)

for event in ("UserPromptSubmit", "PreCompact", "Stop"):
    entries = hooks.get(event, [])
    cleaned = []
    for entry in entries:
        sub = entry.get("hooks", [])
        kept = [h for h in sub if not _is_legacy(h.get("command", ""))]
        if len(kept) != len(sub):
            changed = True
            print(f"  Removed {len(sub) - len(kept)} legacy {event} hook(s)")
        if kept or not sub:
            new_entry = dict(entry)
            if sub:
                new_entry["hooks"] = kept
            cleaned.append(new_entry)
    if entries:
        hooks[event] = cleaned

# Define the bmfote hooks
bmfote_hooks = {
    "UserPromptSubmit": f"{hooks_dir}/bmfote-post-compaction-context.sh",
    "Stop": f"{hooks_dir}/bmfote-stop.sh",
}

for event, script_path in bmfote_hooks.items():
    entries = hooks.setdefault(event, [])

    # Check if bmfote hook already exists in this event
    already = False
    for entry in entries:
        for h in entry.get("hooks", []):
            if "bmfote-" in h.get("command", ""):
                already = True
                break

    if not already:
        entries.append({
            "hooks": [{"type": "command", "command": script_path}]
        })
        changed = True
        print(f"  Added {event} hook")
    else:
        print(f"  {event} hook already configured")

if changed:
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

PYEOF

# --- Step 6: Write config file ---
echo "[6/6] Saving configuration..."

CONFIG_FILE="$HOME/.claude/bmfote.env"
cat > "$CONFIG_FILE" << EOF
BMFOTE_URL=$BMFOTE_URL
BMFOTE_TOKEN=$BMFOTE_TOKEN
EOF
chmod 600 "$CONFIG_FILE"
echo "  Saved to $CONFIG_FILE"

# Clean up legacy shell profile exports (hooks now source bmfote.env directly)
for PROFILE in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.zprofile" "$HOME/.bash_profile"; do
  if [ -f "$PROFILE" ] && grep -q 'export BMFOTE_' "$PROFILE"; then
    sed -i.bak '/^export BMFOTE_URL=/d;/^export BMFOTE_TOKEN=/d' "$PROFILE"
    rm -f "${PROFILE}.bak"
    echo "  Cleaned up legacy exports from $(basename "$PROFILE")"
  fi
done

# --- Done ---
echo ""
echo "Setup complete!"
echo ""
echo "  MCP server:  bmfote-memory → $BMFOTE_URL/mcp/"
echo "  Hooks:       ~/.claude/hooks/bmfote-*.sh"
echo "  Config:      ~/.claude/bmfote.env"
echo "  Database:    $MSG_COUNT messages available"
echo ""
echo "Start a new Claude Code session to use memory tools. No restart needed."
