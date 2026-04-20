#!/usr/bin/env python3
"""FastAPI search server for Claude memory — Turso Cloud (libSQL embedded replica)."""

import hmac
import logging
import math
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


# Recency decay: half-life 14 days matches the typical 2-week dev horizon.
# BM25 is negative (more-negative = better), so multiplying by exp(-age/14)
# pushes old rows toward zero (= worse) under ORDER BY rank ASC. Floor at 0.1
# so strong ancient matches still surface.
DECAY_HALF_LIFE_DAYS = 14.0
DECAY_FLOOR = 0.1


def _apply_recency_decay(rows: list, now: datetime = None) -> list:
    """Re-score rows by bm25 * exp(-age_days / half_life), floored. Returns
    rows sorted ascending by the new rank (most-relevant first)."""
    if not rows:
        return rows
    now = now or datetime.now(timezone.utc)
    for r in rows:
        ts_raw = r.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            decay = max(DECAY_FLOOR, math.exp(-age_days / DECAY_HALF_LIFE_DAYS))
            r["rank"] = r["rank"] * decay
        except (ValueError, AttributeError):
            continue
    rows.sort(key=lambda r: r.get("rank", 0))
    return rows


def query_search(q: str, limit: int = 20, type: str = None, workspace_id: str = None):
    """Full-text search over conversation messages with BM25 ranking and
    recency decay. Over-fetches 3x so decay can promote slightly-weaker but
    fresher matches past the cutoff."""
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
    params.append(limit * 3)
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    rows = _apply_recency_decay(rows)
    return rows[:limit]


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
    """Get recent messages, optionally filtered by session.

    When session_id is given without workspace_id, skip the workspace filter —
    session UUIDs are globally unique, and remote MCP callers don't know which
    workspace owns a session, so forcing the cctx-default fallback drops valid
    rows. Non-session calls still fall back to DEFAULT_WORKSPACE.
    """
    conn = get_conn()
    if session_id:
        if workspace_id:
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
            sql = """
                SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
                       s.project
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.session_id
                WHERE m.session_id = ?
                ORDER BY m.timestamp DESC LIMIT ?
            """
            params = [session_id, limit]
    else:
        workspace_id = workspace_id or DEFAULT_WORKSPACE
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
# DEFINITION EDITS — AI-proposed canonical-doc edits with provenance
# =============================================================

def query_propose_edit(
    uuid: str,
    workspace_id: str,
    file_path: str,
    new_content: str,
    old_content: str = None,
    reason: str = None,
    confidence: float = None,
    source_session_id: str = None,
    source_message_uuid: str = None,
) -> dict:
    """Insert a proposed edit. ON CONFLICT uuid: no-op (idempotent hook retries)."""
    conn = get_conn()
    if source_session_id:
        conn.execute(
            "INSERT INTO sessions (session_id, project) VALUES (?, ?) ON CONFLICT(session_id) DO NOTHING",
            (source_session_id, workspace_id),
        )
    conn.execute(
        """
        INSERT INTO definition_edits (
            uuid, workspace_id, file_path, old_content, new_content,
            reason, confidence, source_session_id, source_message_uuid, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ON CONFLICT(uuid) DO NOTHING
        """,
        (
            uuid, workspace_id, file_path, old_content, new_content,
            reason, confidence, source_session_id, source_message_uuid,
        ),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"uuid": uuid, "status": "pending", "workspace_id": workspace_id}


def query_pending_edits(workspace_id: str = None, limit: int = 50) -> list[dict]:
    """List pending review-queue entries for a workspace, oldest first."""
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    return rows_to_dicts(conn.execute(
        """
        SELECT uuid, workspace_id, file_path, old_content, new_content,
               reason, confidence, source_session_id, source_message_uuid,
               status, created_at
          FROM definition_edits
         WHERE workspace_id = ? AND status = 'pending'
         ORDER BY created_at ASC
         LIMIT ?
        """,
        (workspace_id, limit),
    ))


def query_pending_count(workspace_id: str = None) -> int:
    """Fast count of pending edits — used by session-start banner."""
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM definition_edits WHERE workspace_id = ? AND status = 'pending'",
        (workspace_id,),
    ).fetchone()
    return row[0] if row else 0


def _set_status(edit_uuid: str, workspace_id: str, status: str) -> dict | None:
    """Shared guts for apply/reject. Returns the row after update, or None if
    not found / already-reviewed. Workspace scoping prevents cross-workspace
    mutation even with a guessed UUID."""
    conn = get_conn()
    target = row_to_dict(conn.execute(
        """
        SELECT uuid, workspace_id, file_path, old_content, new_content,
               reason, confidence, source_session_id, source_message_uuid,
               status, created_at, reviewed_at
          FROM definition_edits
         WHERE uuid = ? AND workspace_id = ?
        """,
        (edit_uuid, workspace_id or DEFAULT_WORKSPACE),
    ))
    if target is None:
        return None
    if target["status"] != "pending":
        # Already reviewed — idempotent: return current state
        return target

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE definition_edits SET status = ?, reviewed_at = ? WHERE uuid = ? AND workspace_id = ?",
        (status, now, edit_uuid, workspace_id or DEFAULT_WORKSPACE),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()

    target["status"] = status
    target["reviewed_at"] = now
    return target


def query_apply_edit(edit_uuid: str, workspace_id: str = None) -> dict | None:
    """Mark edit approved. Returns the full row so the CLI can write the
    new_content to disk without a second fetch."""
    return _set_status(edit_uuid, workspace_id, "approved")


def query_reject_edit(edit_uuid: str, workspace_id: str = None) -> dict | None:
    return _set_status(edit_uuid, workspace_id, "rejected")


def query_edit_history(
    workspace_id: str = None,
    file_path: str = None,
    limit: int = 50,
) -> list[dict]:
    """Audit trail. All statuses unless filtered. Most-recent first."""
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    sql = """
        SELECT uuid, workspace_id, file_path, old_content, new_content,
               reason, confidence, source_session_id, source_message_uuid,
               status, created_at, reviewed_at
          FROM definition_edits
         WHERE workspace_id = ?
    """
    params: list = [workspace_id]
    if file_path:
        sql += " AND file_path = ?"
        params.append(file_path)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, tuple(params)))


# =============================================================
# TRACKED FILES + DEFINITION FILES — team-shareable registries
# =============================================================

def query_upsert_tracked_file(
    workspace_id: str,
    file_path: str,
    tracked_by_session: str = None,
) -> dict:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO tracked_files (workspace_id, file_path, tracked_by_session)
        VALUES (?, ?, ?)
        ON CONFLICT(workspace_id, file_path) DO NOTHING
        """,
        (workspace_id, file_path, tracked_by_session),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"workspace_id": workspace_id, "file_path": file_path, "status": "tracked"}


def query_remove_tracked_file(workspace_id: str, file_path: str) -> dict:
    conn = get_conn()
    conn.execute(
        "DELETE FROM tracked_files WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, file_path),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"workspace_id": workspace_id, "file_path": file_path, "status": "untracked"}


def query_list_tracked_files(workspace_id: str = None) -> list[dict]:
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    return rows_to_dicts(conn.execute(
        "SELECT workspace_id, file_path, tracked_at, tracked_by_session FROM tracked_files WHERE workspace_id = ? ORDER BY tracked_at ASC",
        (workspace_id,),
    ))


def query_upsert_def_file(
    workspace_id: str,
    file_path: str,
    content: str,
    version: int = 1,
    updated_by_session: str = None,
) -> dict:
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO definition_files (workspace_id, file_path, content, version, updated_at, updated_by_session)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, file_path) DO UPDATE SET
            content = excluded.content,
            version = excluded.version,
            updated_at = excluded.updated_at,
            updated_by_session = excluded.updated_by_session
        """,
        (workspace_id, file_path, content, version, now, updated_by_session),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    return {"workspace_id": workspace_id, "file_path": file_path, "version": version}


def query_get_def_file(workspace_id: str, file_path: str) -> dict | None:
    conn = get_conn()
    return row_to_dict(conn.execute(
        "SELECT workspace_id, file_path, content, version, updated_at, updated_by_session FROM definition_files WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, file_path),
    ))


def query_list_def_files(workspace_id: str = None) -> list[dict]:
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    conn = get_conn()
    return rows_to_dicts(conn.execute(
        "SELECT workspace_id, file_path, content, version, updated_at, updated_by_session FROM definition_files WHERE workspace_id = ? ORDER BY updated_at DESC",
        (workspace_id,),
    ))


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
# WORKSPACES — list + rename (supports cctx start picker & rename)
# =============================================================

@app.get("/api/sessions")
@limiter.limit("60/minute")
def list_sessions(
    request: Request,
    workspace_id: str = Query(..., description="Workspace scope (required)"),
    limit: int = Query(default=3, le=20),
    exclude_session_id: str = Query(default=None),
):
    """Last N sessions in a workspace, newest first, with metadata for the
    session-start recap hook. Detects continuation chains by looking for the
    compaction marker in the first user message and linking to a session that
    ended within 60 minutes of this one's first timestamp."""
    conn = get_conn()
    rows = rows_to_dicts(conn.execute(
        """
        SELECT m.session_id,
               MAX(m.timestamp) AS last_timestamp,
               MIN(m.timestamp) AS first_timestamp,
               COUNT(*) AS message_count,
               (SELECT content FROM messages m2
                 WHERE m2.session_id = m.session_id
                   AND m2.workspace_id = m.workspace_id
                   AND m2.type = 'user'
                 ORDER BY m2.timestamp ASC LIMIT 1) AS first_user_message
        FROM messages m
        WHERE m.workspace_id = ?
          AND (? IS NULL OR m.session_id != ?)
        GROUP BY m.session_id
        ORDER BY last_timestamp DESC
        LIMIT ?
        """,
        (workspace_id, exclude_session_id, exclude_session_id, limit),
    ))

    marker = "This session is being continued from a previous conversation"
    for r in rows:
        msg = r.get("first_user_message") or ""
        r["is_continuation"] = marker in msg[:500]
        r["continuation_of"] = None
        if r["is_continuation"] and r.get("first_timestamp"):
            prior = row_to_dict(conn.execute(
                """
                SELECT session_id, MAX(timestamp) AS last_ts
                FROM messages
                WHERE workspace_id = ?
                  AND session_id != ?
                GROUP BY session_id
                HAVING last_ts <= ?
                ORDER BY last_ts DESC
                LIMIT 1
                """,
                (workspace_id, r["session_id"], r["first_timestamp"]),
            ))
            if prior and prior.get("last_ts"):
                try:
                    gap = (
                        datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
                        - datetime.fromisoformat(prior["last_ts"].replace("Z", "+00:00"))
                    ).total_seconds()
                    if 0 <= gap <= 3600:
                        r["continuation_of"] = prior["session_id"]
                except Exception:
                    pass
        # Trim the first-user-message preview — hook only needs a topic hint
        if r.get("first_user_message"):
            r["first_user_message"] = r["first_user_message"][:200]

    return rows


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

class BackfillReq(BaseModel):
    from_slug: str = Field(default="cctx-default", max_length=200, alias="from")

    class Config:
        populate_by_name = True


@app.post("/api/admin/backfill-preview")
@limiter.limit("10/minute")
def backfill_preview(request: Request, r: BackfillReq | None = None):
    """Read-only: show how messages currently tagged `from` would split if
    backfilled to sessions.project. No writes."""
    src = (r.from_slug if r else "cctx-default") or "cctx-default"
    conn = get_conn()
    rows = rows_to_dicts(conn.execute(
        """
        SELECT COALESCE(NULLIF(s.project, ''), '(no project)') AS target,
               COUNT(*) AS n
          FROM messages m
          LEFT JOIN sessions s ON s.session_id = m.session_id
         WHERE m.workspace_id = ?
         GROUP BY target
         ORDER BY n DESC
        """,
        (src,),
    ))
    total = sum(r["n"] for r in rows)
    return {"from": src, "total": total, "split": rows}


@app.post("/api/admin/backfill-workspace")
@limiter.limit("2/minute")
def backfill_workspace(request: Request, r: BackfillReq | None = None):
    """Promote sessions.project to messages.workspace_id for rows still
    tagged with `from` (default 'cctx-default'). Safe + idempotent; returns
    before/after distribution so the caller can eyeball the migration."""
    src = (r.from_slug if r else "cctx-default") or "cctx-default"
    conn = get_conn()

    before = rows_to_dicts(conn.execute(
        "SELECT workspace_id, COUNT(*) AS n FROM messages GROUP BY workspace_id ORDER BY n DESC"
    ))

    conn.execute(
        """
        UPDATE messages
           SET workspace_id = COALESCE(
             (SELECT s.project FROM sessions s WHERE s.session_id = messages.session_id),
             ?
           )
         WHERE workspace_id = ?
           AND EXISTS (
             SELECT 1 FROM sessions s
              WHERE s.session_id = messages.session_id
                AND s.project IS NOT NULL
                AND s.project != ''
           )
        """,
        (src, src),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()

    after = rows_to_dicts(conn.execute(
        "SELECT workspace_id, COUNT(*) AS n FROM messages GROUP BY workspace_id ORDER BY n DESC"
    ))

    return {"status": "ok", "from": src, "before": before, "after": after}


# =============================================================
# DEFINITION EDITS — endpoints
# =============================================================

class DefinitionEditCreate(BaseModel):
    uuid: str = Field(max_length=64)
    workspace_id: str = Field(max_length=200)
    file_path: str = Field(max_length=500)
    new_content: str = Field(max_length=100000)
    old_content: Optional[str] = Field(default=None, max_length=100000)
    reason: Optional[str] = Field(default=None, max_length=2000)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source_session_id: str = Field(max_length=64)
    source_message_uuid: Optional[str] = Field(default=None, max_length=64)


@app.post("/api/definitions/propose")
@limiter.limit("120/minute")
def propose_definition_edit(request: Request, edit: DefinitionEditCreate):
    """Queue an AI-proposed edit for human review. Idempotent on uuid."""
    return query_propose_edit(
        uuid=edit.uuid,
        workspace_id=edit.workspace_id,
        file_path=edit.file_path,
        new_content=edit.new_content,
        old_content=edit.old_content,
        reason=edit.reason,
        confidence=edit.confidence,
        source_session_id=edit.source_session_id,
        source_message_uuid=edit.source_message_uuid,
    )


@app.get("/api/definitions/pending")
@limiter.limit("60/minute")
def list_pending(
    request: Request,
    workspace_id: str = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    return query_pending_edits(workspace_id, limit)


@app.get("/api/definitions/pending-count")
@limiter.limit("120/minute")
def pending_count(
    request: Request,
    workspace_id: str = Query(default=None),
):
    """Fast count for the session-start banner — avoids shipping full content."""
    return {"workspace_id": workspace_id or DEFAULT_WORKSPACE, "count": query_pending_count(workspace_id)}


@app.post("/api/definitions/{edit_uuid}/apply")
@limiter.limit("60/minute")
def apply_edit(
    request: Request,
    edit_uuid: str,
    workspace_id: str = Query(default=None),
):
    result = query_apply_edit(edit_uuid, workspace_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "edit not found"})
    return result


@app.post("/api/definitions/{edit_uuid}/reject")
@limiter.limit("60/minute")
def reject_edit(
    request: Request,
    edit_uuid: str,
    workspace_id: str = Query(default=None),
):
    result = query_reject_edit(edit_uuid, workspace_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "edit not found"})
    return result


@app.get("/api/definitions/history")
@limiter.limit("60/minute")
def edit_history(
    request: Request,
    workspace_id: str = Query(default=None),
    file_path: str = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    return query_edit_history(workspace_id, file_path, limit)


# =============================================================
# TRACKED FILES + DEFINITION FILES — endpoints
# =============================================================

class TrackedFileCreate(BaseModel):
    workspace_id: str = Field(max_length=200)
    file_path: str = Field(max_length=500)
    session_id: Optional[str] = Field(default=None, max_length=64)


class DefFileUpsert(BaseModel):
    workspace_id: str = Field(max_length=200)
    file_path: str = Field(max_length=500)
    content: str = Field(max_length=200000)
    version: int = Field(default=1, ge=1)
    session_id: Optional[str] = Field(default=None, max_length=64)


@app.post("/api/tracked-files")
@limiter.limit("60/minute")
def register_tracked_file(request: Request, body: TrackedFileCreate):
    return query_upsert_tracked_file(body.workspace_id, body.file_path, body.session_id)


@app.delete("/api/tracked-files")
@limiter.limit("60/minute")
def unregister_tracked_file(
    request: Request,
    workspace_id: str = Query(),
    file_path: str = Query(),
):
    return query_remove_tracked_file(workspace_id, file_path)


@app.get("/api/tracked-files")
@limiter.limit("60/minute")
def list_tracked_files(
    request: Request,
    workspace_id: str = Query(default=None),
):
    return query_list_tracked_files(workspace_id)


@app.post("/api/def-files")
@limiter.limit("120/minute")
def upsert_def_file(request: Request, body: DefFileUpsert):
    return query_upsert_def_file(
        body.workspace_id, body.file_path, body.content, body.version, body.session_id,
    )


@app.get("/api/def-files")
@limiter.limit("60/minute")
def list_def_files(
    request: Request,
    workspace_id: str = Query(default=None),
):
    return query_list_def_files(workspace_id)


@app.get("/api/def-files/{file_path:path}")
@limiter.limit("60/minute")
def get_def_file(
    request: Request,
    file_path: str,
    workspace_id: str = Query(default=None),
):
    result = query_get_def_file(workspace_id or DEFAULT_WORKSPACE, file_path)
    if result is None:
        raise HTTPException(status_code=404, detail="Definition file not found")
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
