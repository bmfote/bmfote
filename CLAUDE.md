# bmfote

Cloud-synced experiential memory for AI agents, powered by Turso (libSQL).

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
- **Cloud/Docker** (`BMFOTE_REMOTE_DB=1` or `RAILWAY_ENVIRONMENT` set): direct connection to Turso, no local replica. Fails closed if `API_TOKEN` is unset.

## libsql Quirks

- `conn.execute(sql, params)` requires `params` as a **tuple**, not a list
- Embedded replica: `conn.sync()` after writes to push to Turso Cloud
- FTS5 triggers fire normally — same as standard SQLite
