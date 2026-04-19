#!/bin/bash
# Shared workspace-id resolver for cctx hooks.
# Sourced by post-compaction-context.sh and stop-recap.sh.
#
# Usage:
#   source "$(dirname "$0")/lib/resolve-workspace.sh"  # or cctx-lib/resolve-workspace.sh when installed
#   resolve_workspace "$HOOK_INPUT_JSON"
#   # After return: $WORKSPACE_ID is set; CCTX_WORKSPACE is exported.
#
#   resolve_cwd "$HOOK_INPUT_JSON"
#   # After return: $RESOLVED_CWD is set (empty string if unresolvable).
#
# Priority: $CCTX_WORKSPACE env → transcript-path derivation → cctx-default.

resolve_workspace() {
  local input="$1"
  local project

  project=$(printf '%s' "$input" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tp = data.get('transcript_path', '')
parts = tp.split('/projects/')
if len(parts) > 1:
    project_dir = parts[1].split('/')[0]
    if 'github-projects-' in project_dir:
        print(project_dir.split('github-projects-')[-1])
    elif project_dir.startswith('-Users-'):
        print('home')
    else:
        print(project_dir)
else:
    print('')
" 2>/dev/null || echo "")

  WORKSPACE_ID="${CCTX_WORKSPACE:-${project:-cctx-default}}"
  export CCTX_WORKSPACE="$WORKSPACE_ID"
}

resolve_cwd() {
  local input="$1"

  RESOLVED_CWD=$(printf '%s' "$input" | python3 -c "
import os, sys, json

data = json.load(sys.stdin)
tp = data.get('transcript_path', '')
parts = tp.split('/projects/')
if len(parts) < 2:
    sys.exit(0)

encoded = parts[1].split('/')[0]
if not encoded.startswith('-'):
    sys.exit(0)

segments = encoded[1:].split('-')
path = ''
i = 0
while i < len(segments):
    candidate = path + '/' + segments[i]
    if os.path.isdir(candidate):
        path = candidate
        i += 1
        continue

    found = False
    for j in range(i + 1, len(segments)):
        for sep in ('_', '-'):
            joined = sep.join(segments[i:j+1])
            candidate = path + '/' + joined
            if os.path.isdir(candidate):
                path = candidate
                i = j + 1
                found = True
                break
        if found:
            break

    if not found:
        sys.exit(0)

if path and os.path.isdir(path):
    print(path)
" 2>/dev/null || echo "")
}
