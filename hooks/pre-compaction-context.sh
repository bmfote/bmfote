#!/bin/bash
# PreCompact hook — DISABLED for testing (2026-04-11).
# The post-compaction hook handles context recovery after compaction.
# This hook tried to enrich the compaction summary, but the summarizer
# already has the conversation context it's summarizing. Testing whether
# disabling this degrades compaction quality. If not, delete this file.
exit 0
