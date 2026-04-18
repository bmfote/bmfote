#!/bin/bash
# Shared workspace-id resolver for cctx hooks.
# Sourced by post-compaction-context.sh and stop-recap.sh.
#
# Usage:
#   source "$(dirname "$0")/lib/resolve-workspace.sh"  # or cctx-lib/resolve-workspace.sh when installed
#   resolve_workspace "$HOOK_INPUT_JSON"
#   # After return: $WORKSPACE_ID is set; CCTX_WORKSPACE is exported.
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
    if 'github_projects-' in project_dir:
        print(project_dir.split('github_projects-')[-1])
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
