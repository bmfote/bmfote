#!/usr/bin/env python3
"""FastAPI search server for Claude memory — Turso Cloud (libSQL embedded replica)."""

import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

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

# Fail closed: refuse to start without auth on cloud deploys
if is_remote_db() and not API_TOKEN:
    raise RuntimeError("API_TOKEN must be set on cloud deploys")

logger = logging.getLogger("bmfote")
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


app = FastAPI(title="bmfote Memory API", version="1.0.0", lifespan=lifespan)

# Mount MCP — its lifespan is managed by the parent app above
app.mount("/mcp", mcp_app)

# --- Rate limiting ---
def _get_real_ip(request: Request) -> str:
    """Get client IP, preferring X-Forwarded-For behind a reverse proxy."""
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


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60].rstrip("-")


# =============================================================
# Bearer token auth — protects /api/ and /mcp/ when API_TOKEN is set
# =============================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if API_TOKEN and (path.startswith("/api/") or path.startswith("/mcp/")):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != API_TOKEN:
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


@app.get("/health")
def health():
    return {"status": "ok"}


# =============================================================
# QUERY FUNCTIONS — shared by REST endpoints and MCP tools
# =============================================================

def query_search(q: str, limit: int = 20, type: str = None):
    """Full-text search over conversation messages with BM25 ranking."""
    conn = get_conn()
    sql = """
        SELECT m.uuid, m.session_id, m.type, m.role, m.timestamp, m.model,
               s.project,
               snippet(messages_fts, 0, '>>>', '<<<', '...', 40) as snippet,
               bm25(messages_fts) as rank
        FROM messages_fts f
        JOIN messages m ON f.rowid = m.id
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE messages_fts MATCH ?
    """
    params: list = [q]
    if type:
        sql += " AND m.type = ?"
        params.append(type)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return rows_to_dicts(conn.execute(sql, tuple(params)))


def query_similar_error(error: str, limit: int = 5):
    """Find past errors and their solutions."""
    conn = get_conn()
    error_matches = rows_to_dicts(conn.execute("""
        SELECT m.uuid, m.session_id, m.content, m.timestamp,
               s.project,
               bm25(messages_fts) as rank
        FROM messages_fts f
        JOIN messages m ON f.rowid = m.id
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE messages_fts MATCH ?
          AND m.type = 'user'
        ORDER BY rank
        LIMIT ?
    """, (error, limit)))

    results = []
    for err in error_matches:
        solution = row_to_dict(conn.execute("""
            SELECT content, timestamp
            FROM messages
            WHERE parent_uuid = ? AND type = 'assistant'
            LIMIT 1
        """, (err["uuid"],)))
        results.append({
            "error_context": err["content"][:800],
            "project": err["project"],
            "timestamp": err["timestamp"],
            "session_id": err["session_id"],
            "uuid": err["uuid"],
            "solution": solution["content"][:800] if solution else None,
        })
    return results


def query_message(uuid: str, context: int = 1):
    """Get full message content by UUID, with optional surrounding context."""
    conn = get_conn()
    target = row_to_dict(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.role, m.content,
               m.timestamp, m.model, s.project
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.uuid = ?
    """, (uuid,)))

    if not target:
        return None

    if context == 0:
        return target

    context_rows = rows_to_dicts(conn.execute("""
        SELECT uuid, type, role, content, timestamp, model
        FROM messages
        WHERE session_id = ? AND uuid != ?
        ORDER BY timestamp
    """, (target["session_id"], uuid)))

    before = [m for m in context_rows if m["timestamp"] <= target["timestamp"]]
    after = [m for m in context_rows if m["timestamp"] > target["timestamp"]]

    return {**target, "before": before[-context:], "after": after[:context]}


def query_recent(hours: int = 24, limit: int = 50, session_id: str = None):
    """Get recent messages, optionally filtered by session."""
    conn = get_conn()
    if session_id:
        sql = """
            SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
                   s.project
            FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.session_id
            WHERE m.session_id = ?
            ORDER BY m.timestamp DESC LIMIT ?
        """
        params: list = [session_id, limit]
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql = """
            SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
                   s.project
            FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.session_id
            WHERE m.timestamp > ?
            ORDER BY m.timestamp DESC LIMIT ?
        """
        params = [cutoff, limit]
    return rows_to_dicts(conn.execute(sql, tuple(params)))


def query_vault_search(q: str, project: str = None, doc_type: str = None,
                       outcome: str = None, limit: int = 10):
    """Faceted full-text search over curated vault content with weighted BM25."""
    conn = get_conn()
    sql = """
        SELECT v.file_path, v.project, v.topic, v.date, v.outcome,
               v.tags, v.doc_type,
               snippet(vault_fts, 2, '>>>', '<<<', '...', 40) as snippet,
               bm25(vault_fts, 10.0, 5.0, 1.0, 3.0) as rank
        FROM vault_fts f
        JOIN vault_docs v ON f.rowid = v.id
        WHERE vault_fts MATCH ?
    """
    params: list = [q]
    if project:
        sql += " AND v.project = ?"
        params.append(project)
    if doc_type:
        sql += " AND v.doc_type = ?"
        params.append(doc_type)
    if outcome:
        sql += " AND v.outcome = ?"
        params.append(outcome)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
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
):
    try:
        return query_search(q, limit, type)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid search query"})


@app.get("/api/similar-error")
@limiter.limit("60/minute")
def similar_error(
    request: Request,
    error: str,
    limit: int = Query(default=5, le=20),
):
    try:
        return query_similar_error(error, limit)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid search query"})


@app.get("/api/message/{uuid}")
@limiter.limit("60/minute")
def get_message(
    request: Request,
    uuid: str,
    context: int = Query(default=1, ge=0, le=10),
):
    result = query_message(uuid, context)
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
):
    return query_recent(hours, limit, session_id)


@app.get("/api/project/{project_name}")
@limiter.limit("60/minute")
def project_messages(
    request: Request,
    project_name: str,
    limit: int = Query(default=20, le=100),
):
    """Get recent messages from a specific project."""
    conn = get_conn()
    return rows_to_dicts(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.content, m.timestamp
        FROM messages m
        JOIN sessions s ON m.session_id = s.session_id
        WHERE s.project = ?
        ORDER BY m.timestamp DESC
        LIMIT ?
    """, (project_name, limit)))


@app.get("/api/stats")
@limiter.limit("60/minute")
def stats(request: Request):
    """Database statistics."""
    conn = get_conn()
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    vault_count = conn.execute("SELECT COUNT(*) FROM vault_docs").fetchone()[0]

    date_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM messages"
    ).fetchone()

    return {
        "messages": msg_count,
        "sessions": session_count,
        "vault_docs": vault_count,
        "first_message": date_range[0],
        "last_message": date_range[1],
    }


# =============================================================
# VAULT SEARCH ENDPOINTS
# =============================================================

@app.get("/api/vault/search")
@limiter.limit("60/minute")
def vault_search(
    request: Request,
    q: str,
    project: str = Query(default=None),
    doc_type: str = Query(default=None),
    outcome: str = Query(default=None),
    limit: int = Query(default=10, le=50),
):
    try:
        return query_vault_search(q, project, doc_type, outcome, limit)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid search query"})


@app.get("/api/vault/doc/{file_path:path}")
@limiter.limit("60/minute")
def vault_doc(request: Request, file_path: str):
    """Get full vault document content by path."""
    conn = get_conn()
    result = row_to_dict(conn.execute(
        "SELECT * FROM vault_docs WHERE file_path = ?", (file_path,)
    ))
    if not result:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return result


@app.get("/api/vault/stats")
@limiter.limit("60/minute")
def vault_stats(request: Request):
    """Vault statistics."""
    conn = get_conn()
    by_type = rows_to_dicts(conn.execute(
        "SELECT doc_type, COUNT(*) as count FROM vault_docs GROUP BY doc_type"
    ))
    by_project = rows_to_dicts(conn.execute(
        "SELECT project, COUNT(*) as count FROM vault_docs WHERE project IS NOT NULL GROUP BY project"
    ))
    by_outcome = rows_to_dicts(conn.execute(
        "SELECT outcome, COUNT(*) as count FROM vault_docs WHERE outcome IS NOT NULL GROUP BY outcome"
    ))

    return {
        "total_docs": sum(r["count"] for r in by_type),
        "by_type": by_type,
        "by_project": by_project,
        "by_outcome": by_outcome,
    }


@app.get("/api/vault/list")
@limiter.limit("60/minute")
def vault_list(
    request: Request,
    project: str = Query(default=None),
    doc_type: str = Query(default=None),
    outcome: str = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """List vault documents with optional filters."""
    conn = get_conn()
    sql = """
        SELECT file_path, project, topic, date, outcome, tags, doc_type
        FROM vault_docs WHERE 1=1
    """
    params: list = []

    if project:
        sql += " AND project = ?"
        params.append(project)
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    if outcome:
        sql += " AND outcome = ?"
        params.append(outcome)

    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    return rows_to_dicts(conn.execute(sql, tuple(params)))


class ArchiveCreate(BaseModel):
    project: str
    topic: str
    date: str
    outcome: str
    tags: List[str] = []
    content: str = Field(max_length=100000)
    doc_type: str = "session"
    workflows_touched: List[str] = []


@app.post("/api/vault/archive")
@limiter.limit("20/minute")
def create_archive(request: Request, archive: ArchiveCreate):
    """Create a new session archive directly in the vault_docs table."""
    conn = get_conn()
    slug = slugify(archive.topic)
    file_path = f"{archive.project}/sessions/{archive.date}_{slug}.md"

    frontmatter = {
        "project": archive.project,
        "topic": archive.topic,
        "date": archive.date,
        "outcome": archive.outcome,
        "tags": archive.tags,
        "archived": archive.date,
    }
    if archive.workflows_touched:
        frontmatter["workflows_touched"] = archive.workflows_touched

    checksum = hashlib.md5(archive.content.encode()).hexdigest()

    existing = row_to_dict(conn.execute(
        "SELECT id FROM vault_docs WHERE file_path = ?", (file_path,)
    ))

    if existing:
        conn.execute("""
            UPDATE vault_docs SET
                project=?, topic=?, date=?, outcome=?, tags=?,
                doc_type=?, content=?, frontmatter_json=?,
                last_modified=?, checksum=?
            WHERE file_path=?
        """, (
            archive.project, archive.topic, archive.date,
            archive.outcome, json.dumps(archive.tags),
            archive.doc_type, archive.content,
            json.dumps(frontmatter), time.time(), checksum,
            file_path,
        ))
    else:
        conn.execute("""
            INSERT INTO vault_docs (
                file_path, project, topic, date, outcome, tags,
                doc_type, content, frontmatter_json, last_modified, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            file_path, archive.project, archive.topic, archive.date,
            archive.outcome, json.dumps(archive.tags),
            archive.doc_type, archive.content,
            json.dumps(frontmatter), time.time(), checksum,
        ))

    conn.commit()
    if not is_remote_db():
        conn.sync()

    return {
        "file_path": file_path,
        "status": "updated" if existing else "created",
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


@app.post("/api/messages")
@limiter.limit("200/minute")
def create_message(request: Request, msg: MessageCreate):
    """Write a message to the shared memory. Returns the UUID."""
    conn = get_conn()
    ts = msg.timestamp or datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO messages (uuid, session_id, parent_uuid, type, role,
                              content, model, input_tokens, output_tokens, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            content=excluded.content, model=excluded.model,
            input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens
    """, (
        msg.uuid, msg.session_id, msg.parent_uuid, msg.type, msg.role,
        msg.content, msg.model, msg.input_tokens, msg.output_tokens, ts,
    ))
    conn.commit()
    if not is_remote_db():
        conn.sync()

    return {"uuid": msg.uuid, "status": "ok"}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
