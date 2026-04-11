# bmfote

Cloud-synced experiential memory for AI agents, powered by Turso (libSQL).

## Architecture

- **Engine**: FastAPI server at `engine/server.py` — 12 REST endpoints (search, vault, sync)
- **Database**: Turso Cloud with local embedded replica for fast reads
- **FTS**: Standard SQLite FTS5 (NOT Tantivy) — `bm25()`, `snippet()`, `MATCH` syntax
- **Connection**: `libsql_experimental` Python SDK (requires tuples for params, not lists)

## Key Files

- `engine/server.py` — API server (port 8026 during dev, port 8025 is the existing system)
- `engine/schema.sql` — Turso-compatible schema (FTS5 + triggers)
- `engine/sync_conversations.py` — Incremental JSONL → Turso sync
- `engine/sync_vault.py` — Obsidian vault markdown → Turso sync
- `engine/seed_turso.py` — One-time migration from local SQLite to Turso
- `.env` — Turso credentials (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, PORT)

## Development

```bash
source .venv/bin/activate  # Python 3.12 (libsql lacks 3.14 wheels)
python engine/server.py    # Starts on PORT from .env (default 8026)
```

## Critical Constraint

The existing memory system at `~/claude-conversations-db/` (port 8025) must NOT be modified.
This system runs alongside it on port 8026 until cutover validation is complete.

## libsql Quirks

- `conn.execute(sql, params)` requires `params` as a **tuple**, not a list
- Embedded replica: `conn.sync()` after writes to push to Turso Cloud
- FTS5 triggers fire normally — same as standard SQLite
