# cctx

Cloud context for AI agents, powered by Turso (libSQL). SQLite FTS5, hooks auto-capture, <100ms retrieval.

## Context / Persistence (read this first)

This project IS the context system. Persist context via cctx itself — **do not write to `~/.claude/projects/.../memory/*.md`** for this repo.

- Production endpoint: `https://bmfote-api-production-7a63.up.railway.app` (Railway)
- MCP tools: `mcp__cctx-memory__remember`, `search_memory`, `get_recent`, `get_context`, `find_error`
- Local dev server: `http://localhost:8026` (when `python -m engine.server` is running)

When recalling prior conversations or saving new context, use the MCP tools or REST API below — not the markdown auto-memory system described in the global system prompt.

### REST API quick reference

All endpoints require `Authorization: Bearer $CCTX_TOKEN`. Source env with `source ~/.claude/bmfote.env` first (production config file is still named bmfote.env until installer is re-run).

**Read:**
```bash
# Search (FTS5)
curl -s -H "Authorization: Bearer $BMFOTE_TOKEN" "$BMFOTE_URL/api/search?q=QUERY"

# Recent messages
curl -s -H "Authorization: Bearer $BMFOTE_TOKEN" "$BMFOTE_URL/api/recent?hours=24&limit=50"

# Full message by UUID
curl -s -H "Authorization: Bearer $BMFOTE_TOKEN" "$BMFOTE_URL/api/message/UUID?context=1"

# Stats
curl -s -H "Authorization: Bearer $BMFOTE_TOKEN" "$BMFOTE_URL/api/stats"
```

**Write (2-step: create session, then post message):**
```bash
# 1. Create/upsert session (idempotent)
curl -s -X POST "$BMFOTE_URL/api/sessions" \
  -H "Authorization: Bearer $BMFOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "my-session-id", "project": "my-project"}'

# 2. Write a message into that session
curl -s -X POST "$BMFOTE_URL/api/messages" \
  -H "Authorization: Bearer $BMFOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "my-session-id",
    "uuid": "unique-msg-id",
    "type": "assistant",
    "role": "assistant",
    "content": "The text to persist",
    "timestamp": "2026-04-16T07:30:00Z"
  }'
```

The `uuid` field must be globally unique. `ON CONFLICT(uuid)` updates content but not workspace_id. Omit `workspace_id` to use the default (`cctx-default`). Omit `timestamp` to use server time.

## Session-start context

The cctx hook injects a `PRIOR_SESSIONS` list (most recent first) into the system reminders on the **first** UserPromptSubmit of a session. When you see it, call `get_recent(session_id=<#1>, workspace="<current>")` to pull the last ~50 messages of the most recent prior session as background context for the user's prompt. Don't produce an unsolicited recap — just use the context to answer.

## Architecture

- **Engine**: FastAPI server at `engine/server.py` — 9 REST endpoints (search, messages, sync)
- **Database**: Turso Cloud with local embedded replica for fast reads
- **FTS**: Standard SQLite FTS5 (NOT Tantivy) — `bm25()`, `snippet()`, `MATCH` syntax
- **Connection**: `libsql_experimental` Python SDK (requires tuples for params, not lists)

## Key Files

- `engine/server.py` — API server (port 8026 during dev)
- `engine/mcp_server.py` — FastMCP tools; calls shared query functions from server.py
- `engine/db.py` — Shared libSQL connection layer; `is_remote_db()` switches between embedded replica (local dev) and direct Turso (Docker/cloud)
- `engine/schema.sql` — Turso-compatible schema (FTS5 + triggers)
- `engine/sync_conversations.py` — Incremental JSONL → Turso sync (local dev utility)
- `Dockerfile` — Deployment artifact; any Docker-compatible host (Railway/Fly/Render/bare Docker)
- `installer/setup.sh` — Per-machine Claude Code client config
- `.env` — Turso credentials (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, PORT)

## Development

```bash
source .venv/bin/activate  # Python 3.12 (libsql lacks 3.14 wheels)
python -m engine.server    # Starts on PORT from .env (default 8026)
```

## Cloud vs local mode

- **Local dev** (no env vars): uses embedded replica at `engine/local-replica.db` with sync to Turso.
- **Cloud/Docker** (`CCTX_REMOTE_DB=1` or `RAILWAY_ENVIRONMENT` set): direct connection to Turso, no local replica. Fails closed if `API_TOKEN` is unset.

## libsql Quirks

- `conn.execute(sql, params)` requires `params` as a **tuple**, not a list
- Embedded replica: `conn.sync()` after writes to push to Turso Cloud
- FTS5 triggers fire normally — same as standard SQLite

<!-- cctx:start -->
## Memory / Persistence (read this first)

This project uses **cctx** for cross-session context — **do not write to `~/.claude/projects/.../memory/*.md`** for this repo. Use the MCP tools or REST API.

- Workspace: `bmfote`
- Endpoint: `https://bmfote-api-production-7a63.up.railway.app`
- MCP tools: `mcp__cctx-memory__remember`, `search_memory`, `get_recent`, `get_context`, `find_error`
- Shell fallback: `source ~/.claude/cctx.env && curl -H "Authorization: Bearer $CCTX_TOKEN" "$CCTX_URL/api/search?q=QUERY&workspace_id=bmfote"`

When recalling prior conversations or saving new context, use cctx — not the markdown auto-memory system described in the global system prompt.

## Session-start context

The cctx hook injects a `PRIOR_SESSIONS` list (most recent first) into the system reminders on the **first** UserPromptSubmit of a session. When you see it, call `get_recent(session_id=<#1>, workspace="bmfote")` to pull the last ~50 messages of the most recent prior session as background context for the user's prompt. Don't produce an unsolicited recap — just use the context to answer.
<!-- cctx:end -->
