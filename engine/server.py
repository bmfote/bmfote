#!/usr/bin/env python3
"""FastAPI search server for Claude memory — Turso Cloud (libSQL embedded replica)."""

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from engine.db import get_conn, is_remote_db, rows_to_dicts, row_to_dict
from engine.mcp_server import mcp

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN", "")
PORT = int(os.getenv("PORT", "8026"))
DEFAULT_WORKSPACE = "cctx-default"

# Fail closed: refuse to start without auth on cloud deploys
if is_remote_db() and not API_TOKEN:
    raise RuntimeError("API_TOKEN must be set on cloud deploys")

logger = logging.getLogger("cctx")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Build the MCP sub-app (initializes session_manager) then wire its lifespan
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="cctx Memory API", version="1.0.0", lifespan=lifespan, redoc_url=None)

# Mount MCP — its lifespan is managed by the parent app above
app.mount("/mcp", mcp_app)

# --- Rate limiting ---
def _get_real_ip(request: Request) -> str:
    """Get client IP, preferring X-Forwarded-For behind a trusted reverse proxy."""
    if is_remote_db():
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
app.state.limiter = limiter


app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "rate limit exceeded", "detail": str(exc.detail)},
    )


# CORS for dashboard access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================
# Bearer token auth — protects /api/ and /mcp/ when API_TOKEN is set
# =============================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if API_TOKEN and (path.startswith("/api/") or path.startswith("/mcp/")):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], API_TOKEN):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)


# =============================================================
# Request logging
# =============================================================

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    logger.info(
        "%s %s %s %dms %s",
        request.client.host if request.client else "-",
        request.method,
        request.url.path,
        elapsed,
        response.status_code,
    )
    return response


@app.get("/")
def root():
    """Zero-effort discovery. Lists core endpoints so a fresh agent doesn't
    have to curl /, /api, /search and parse OpenAPI before it can search."""
    return {
        "name": "cctx memory API",
        "search": "/api/search?q=QUERY",
        "fetch": "/api/message/{uuid}?context=1",
        "recent": "/api/recent?hours=24",
        "spec": "/openapi.json",
    }


@app.get("/health")
def health():
    try:
        get_conn().execute("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": "database unreachable"})
    return {"status": "ok"}


# =============================================================
# QUERY FUNCTIONS — shared by REST endpoints and MCP tools
# =============================================================

def _auto_phrase(q: str) -> str:
    """Three-tier fallback for bare multi-word queries: exact phrase match
    (highest BM25), NEAR/5 proximity (words close together), OR expansion
    (any token present). Single tokens stay phrase-quoted. Callers passing
    explicit FTS5 syntax pass through unchanged."""
    if not q or any(c in q for c in '"*():^'):
        return q
    tokens = q.split()
    if any(op in tokens for op in ("AND", "OR", "NOT", "NEAR")):
        return q
    if len(tokens) == 1:
        return f'"{q}"'
    # Quote tokens containing FTS5-special chars (dots, colons, hyphens)
    safe = [f'"{t}"' if any(c in t for c in ".:-/") else t for t in tokens]
    return f'"{q}" OR NEAR({" ".join(safe)}, 5) OR ({" OR ".join(safe)})'


def query_search(q: str, limit: int = 20, type: str = None, workspace_id: str = None):
    """Full-text search over conversation messages with BM25 ranking."""
    q = _auto_phrase(q)
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    sql = """
        SELECT m.uuid, m.session_id, m.type, m.role, m.timestamp, m.model,
               s.project,
               snippet(messages_fts, 0, '>>>', '<<<', '...', 40) as snippet,
               bm25(messages_fts) as rank
        FROM messages_fts f
        JOIN messages m ON f.rowid = m.id
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE messages_fts MATCH ? AND m.workspace_id = ?
    """
    params: list = [q, workspace_id]
    if type:
        sql += " AND m.type = ?"
        params.append(type)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, tuple(params)))


def query_similar_error(error: str, limit: int = 5, workspace_id: str = None):
    """Find past errors and their solutions."""
    error = _auto_phrase(error)
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    results = rows_to_dicts(conn.execute("""
        SELECT e.content AS error_content, e.project, e.timestamp,
               e.session_id, e.uuid,
               (SELECT m.content FROM messages m
                WHERE m.parent_uuid = e.uuid AND m.type = 'assistant'
                  AND m.workspace_id = ?
                LIMIT 1) AS solution_content
        FROM (
            SELECT m.uuid, m.content, m.timestamp, m.session_id, s.project
            FROM messages_fts fts
            JOIN messages m ON m.id = fts.rowid
            LEFT JOIN sessions s ON m.session_id = s.session_id
            WHERE messages_fts MATCH ? AND m.type = 'user' AND m.workspace_id = ?
            ORDER BY bm25(messages_fts)
            LIMIT ?
        ) e
    """, (workspace_id, error, workspace_id, limit)))

    return [{
        "error_context": r["error_content"][:800],
        "project": r["project"],
        "timestamp": r["timestamp"],
        "session_id": r["session_id"],
        "uuid": r["uuid"],
        "solution": r["solution_content"][:800] if r.get("solution_content") else None,
    } for r in results]


def query_message(uuid: str, context: int = 1, workspace_id: str = None):
    """Get full message content by UUID, with optional surrounding context.

    Workspace isolation is enforced: a UUID in workspace A is not visible to
    callers scoped to workspace B, even if they guess the UUID correctly.
    """
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    target = row_to_dict(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.role, m.content,
               m.timestamp, m.model, s.project
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.uuid = ? AND m.workspace_id = ?
    """, (uuid, workspace_id)))

    if not target:
        return None

    if context == 0:
        return target

    before = rows_to_dicts(conn.execute("""
        SELECT uuid, type, role, content, timestamp, model
        FROM messages
        WHERE session_id = ? AND uuid != ? AND workspace_id = ?
          AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (target["session_id"], uuid, workspace_id, target["timestamp"], context)))

    after = rows_to_dicts(conn.execute("""
        SELECT uuid, type, role, content, timestamp, model
        FROM messages
        WHERE session_id = ? AND uuid != ? AND workspace_id = ?
          AND timestamp > ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (target["session_id"], uuid, workspace_id, target["timestamp"], context)))

    before = list(reversed(before))
    return {**target, "before": before, "after": after}


def query_recent(hours: int = 24, limit: int = 50, session_id: str = None, workspace_id: str = None):
    """Get recent messages, optionally filtered by session."""
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    if session_id:
        sql = """
            SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
                   s.project
            FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.session_id
            WHERE m.session_id = ? AND m.workspace_id = ?
            ORDER BY m.timestamp DESC LIMIT ?
        """
        params: list = [session_id, workspace_id, limit]
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql = """
            SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
                   s.project
            FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.session_id
            WHERE m.timestamp > ? AND m.workspace_id = ?
            ORDER BY m.timestamp DESC LIMIT ?
        """
        params = [cutoff, workspace_id, limit]
    return rows_to_dicts(conn.execute(sql, tuple(params)))


# =============================================================
# CONVERSATION SEARCH ENDPOINTS
# =============================================================

@app.get("/api/search")
@limiter.limit("60/minute")
def search_messages(
    request: Request,
    q: str,
    limit: int = Query(default=20, le=100),
    type: str = Query(default=None, description="Filter by 'user' or 'assistant'"),
    workspace_id: str = Query(default=None, description="Workspace scope (defaults to 'cctx-default')"),
):
    try:
        return query_search(q, limit, type, workspace_id)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid search query"})


@app.get("/api/similar-error")
@limiter.limit("60/minute")
def similar_error(
    request: Request,
    error: str,
    limit: int = Query(default=5, le=20),
    workspace_id: str = Query(default=None),
):
    try:
        return query_similar_error(error, limit, workspace_id)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid search query"})


@app.get("/api/message/{uuid}")
@limiter.limit("60/minute")
def get_message(
    request: Request,
    uuid: str,
    context: int = Query(default=1, ge=0, le=10),
    workspace_id: str = Query(default=None),
):
    result = query_message(uuid, context, workspace_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "message not found"})
    return result


@app.get("/api/recent")
@limiter.limit("60/minute")
def recent_messages(
    request: Request,
    hours: int = Query(default=24, le=168),
    limit: int = Query(default=50, le=200),
    session_id: str = Query(default=None),
    workspace_id: str = Query(default=None),
):
    return query_recent(hours, limit, session_id, workspace_id)


@app.get("/api/project/{project_name}")
@limiter.limit("60/minute")
def project_messages(
    request: Request,
    project_name: str,
    limit: int = Query(default=20, le=100),
    workspace_id: str = Query(default=None),
):
    """Get recent messages from a specific project."""
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    return rows_to_dicts(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.content, m.timestamp
        FROM messages m
        JOIN sessions s ON m.session_id = s.session_id
        WHERE s.project = ? AND m.workspace_id = ?
        ORDER BY m.timestamp DESC
        LIMIT ?
    """, (project_name, workspace_id, limit)))


@app.get("/api/stats")
@limiter.limit("60/minute")
def stats(
    request: Request,
    workspace_id: str = Query(default=None, description="Scope stats to a workspace (omit for global)"),
):
    """Database statistics. Omit workspace_id for global counts, provide it for per-workspace."""
    conn = get_conn()
    if workspace_id:
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        date_range = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM messages WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        return {
            "workspace_id": workspace_id,
            "messages": msg_count,
            "first_message": date_range[0],
            "last_message": date_range[1],
        }

    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM messages"
    ).fetchone()

    return {
        "messages": msg_count,
        "sessions": session_count,
        "first_message": date_range[0],
        "last_message": date_range[1],
    }


# =============================================================
# WRITE ENDPOINTS — any machine can push data to shared memory
# =============================================================

class MessageCreate(BaseModel):
    session_id: str
    uuid: str
    content: Optional[str] = Field(default=None, max_length=50000)
    type: str = "user"
    role: Optional[str] = None
    parent_uuid: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    timestamp: Optional[str] = None
    workspace_id: Optional[str] = None


@app.post("/api/messages")
@limiter.limit("200/minute")
def create_message(request: Request, msg: MessageCreate):
    """Write a message to the shared memory. Returns the UUID.

    Note: ON CONFLICT does NOT overwrite workspace_id — a message's workspace
    is fixed at insert time so a caller can't re-post a UUID to move it.
    """
    conn = get_conn()
    ts = msg.timestamp or datetime.now(timezone.utc).isoformat()
    ws = msg.workspace_id or DEFAULT_WORKSPACE

    conn.execute("""
        INSERT INTO messages (uuid, session_id, parent_uuid, type, role,
                              content, model, input_tokens, output_tokens, timestamp,
                              workspace_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            content=excluded.content, model=excluded.model,
            input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens
    """, (
        msg.uuid, msg.session_id, msg.parent_uuid, msg.type, msg.role,
        msg.content, msg.model, msg.input_tokens, msg.output_tokens, ts, ws,
    ))
    conn.commit()
    if not is_remote_db():
        conn.sync()

    return {"uuid": msg.uuid, "status": "ok", "workspace_id": ws}


class SessionCreate(BaseModel):
    session_id: str
    project: Optional[str] = None
    first_message_at: Optional[str] = None
    last_message_at: Optional[str] = None
    message_count: Optional[int] = None


@app.post("/api/sessions")
@limiter.limit("60/minute")
def create_session(request: Request, session: SessionCreate):
    """Create or update session metadata."""
    conn = get_conn()

    conn.execute("""
        INSERT INTO sessions (session_id, project, first_message_at,
                              last_message_at, message_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            project=COALESCE(excluded.project, sessions.project),
            last_message_at=COALESCE(excluded.last_message_at, sessions.last_message_at),
            message_count=COALESCE(excluded.message_count, sessions.message_count)
    """, (
        session.session_id, session.project, session.first_message_at,
        session.last_message_at, session.message_count,
    ))
    conn.commit()
    if not is_remote_db():
        conn.sync()

    return {"session_id": session.session_id, "status": "ok"}


# =============================================================
# BOOKMARKS — name-a-session so you can resume it later
# =============================================================
# One nullable column on `sessions`; no separate table. Global uniqueness
# across workspaces (partial index) because it's fine for a single-tenant
# deploy and collapses cleanly when we add per-workspace bookmarks later.

class BookmarkCreate(BaseModel):
    name: str = Field(max_length=200)
    session_id: str


@app.get("/api/bookmarks")
@limiter.limit("60/minute")
def list_bookmarks(request: Request, limit: int = Query(default=100, le=500)):
    conn = get_conn()
    return rows_to_dicts(conn.execute(
        """
        SELECT s.bookmark_name AS name, s.session_id, s.project,
               COALESCE(
                 (SELECT MAX(timestamp) FROM messages
                  WHERE session_id = s.session_id),
                 s.last_message_at
               ) AS last_active
        FROM sessions s
        WHERE s.bookmark_name IS NOT NULL
        ORDER BY last_active DESC
        LIMIT ?
        """,
        (limit,),
    ))


@app.post("/api/bookmarks")
@limiter.limit("60/minute")
def save_bookmark(request: Request, b: BookmarkCreate):
    conn = get_conn()
    conn.execute(
        "UPDATE sessions SET bookmark_name = NULL WHERE bookmark_name = ?",
        (b.name,),
    )
    conn.execute(
        "UPDATE sessions SET bookmark_name = ? WHERE session_id = ?",
        (b.name, b.session_id),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"status": "ok", "name": b.name}


@app.delete("/api/bookmarks/{name}")
@limiter.limit("60/minute")
def delete_bookmark(request: Request, name: str):
    conn = get_conn()
    conn.execute(
        "UPDATE sessions SET bookmark_name = NULL WHERE bookmark_name = ?",
        (name,),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"status": "ok"}


# =============================================================
# WORKSPACES — list + rename (supports cctx start picker & rename)
# =============================================================

@app.get("/api/workspaces")
@limiter.limit("60/minute")
def list_workspaces(request: Request, limit: int = Query(default=50, le=200)):
    """Distinct workspaces with last activity + message count. Powers the
    cctx start picker and the known-workspaces injection in hooks."""
    conn = get_conn()
    return rows_to_dicts(conn.execute(
        """
        SELECT workspace_id,
               COUNT(*) AS message_count,
               MAX(timestamp) AS last_active
        FROM messages
        WHERE workspace_id IS NOT NULL AND workspace_id != ''
        GROUP BY workspace_id
        ORDER BY last_active DESC
        LIMIT ?
        """,
        (limit,),
    ))


class WorkspaceRename(BaseModel):
    old_id: str = Field(max_length=200)
    new_id: str = Field(max_length=200)


@app.post("/api/workspaces/rename")
@limiter.limit("10/minute")
def rename_workspace(request: Request, r: WorkspaceRename):
    """Rewrite workspace_id across messages (and sessions.project when it
    matches the old slug). Idempotent — no-op if old_id has no rows."""
    if not r.old_id or not r.new_id:
        return JSONResponse(status_code=400, content={"error": "old_id and new_id required"})
    if r.old_id == r.new_id:
        return {"status": "noop", "rows": 0}

    conn = get_conn()
    result_msgs = conn.execute(
        "UPDATE messages SET workspace_id = ? WHERE workspace_id = ?",
        (r.new_id, r.old_id),
    )
    # Only rewrite sessions.project where it matches the old slug — leaves
    # unrelated project labels alone.
    result_sess = conn.execute(
        "UPDATE sessions SET project = ? WHERE project = ?",
        (r.new_id, r.old_id),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()

    return {
        "status": "ok",
        "old_id": r.old_id,
        "new_id": r.new_id,
        "messages_updated": getattr(result_msgs, "rowcount", None),
        "sessions_updated": getattr(result_sess, "rowcount", None),
    }


# =============================================================
# SYNC ENDPOINT
# =============================================================

@app.post("/api/sync")
@limiter.limit("10/minute")
def manual_sync(request: Request):
    """Manually trigger embedded replica sync with Turso Cloud."""
    conn = get_conn()
    if is_remote_db():
        return {"status": "skipped", "reason": "remote connection, no replica to sync"}
    conn.sync()
    return {"status": "synced"}


# =============================================================
# ADMIN — one-shot maintenance endpoints
# =============================================================

@app.post("/api/admin/backfill-workspace")
@limiter.limit("2/minute")
def backfill_workspace(request: Request):
    """Promote sessions.project to messages.workspace_id for rows still
    tagged 'cctx-default'. Safe + idempotent; also returns before/after
    distribution so the caller can eyeball the migration worked."""
    conn = get_conn()

    before = rows_to_dicts(conn.execute(
        "SELECT workspace_id, COUNT(*) AS n FROM messages GROUP BY workspace_id ORDER BY n DESC"
    ))

    conn.execute(
        """
        UPDATE messages
           SET workspace_id = COALESCE(
             (SELECT s.project FROM sessions s WHERE s.session_id = messages.session_id),
             'cctx-default'
           )
         WHERE workspace_id = 'cctx-default'
           AND EXISTS (
             SELECT 1 FROM sessions s
              WHERE s.session_id = messages.session_id
                AND s.project IS NOT NULL
                AND s.project != ''
           )
        """
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()

    after = rows_to_dicts(conn.execute(
        "SELECT workspace_id, COUNT(*) AS n FROM messages GROUP BY workspace_id ORDER BY n DESC"
    ))

    return {"status": "ok", "before": before, "after": after}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
