#!/usr/bin/env python3
"""FastAPI search server for Claude memory — Turso Cloud (libSQL embedded replica)."""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import List, Optional

import libsql_experimental as libsql
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
API_TOKEN = os.getenv("API_TOKEN", "")
PORT = int(os.getenv("PORT", "8026"))
DB_PATH = Path(__file__).parent / "local-replica.db"

app = FastAPI(title="bmfote Memory API", version="1.0.0")

# CORS for dashboard access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================
# Connection layer — embedded replica (local) or remote (Railway)
# =============================================================

def get_connection():
    """Create libSQL connection. Embedded replica locally, remote on Railway."""
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        return libsql.connect(
            database=str(DB_PATH),
            sync_url=TURSO_URL,
            auth_token=TURSO_TOKEN,
        )


# Module-level connection — reused across requests
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = get_connection()
        if not os.getenv("RAILWAY_ENVIRONMENT"):
            _conn.sync()
    return _conn


def rows_to_dicts(cursor) -> list[dict]:
    """Convert libSQL cursor results to list of dicts."""
    if cursor.description is None:
        return []
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def row_to_dict(cursor) -> Optional[dict]:
    """Fetch one row as dict, or None."""
    if cursor.description is None:
        return None
    columns = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60].rstrip("-")


# =============================================================
# Optional bearer token auth
# =============================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if API_TOKEN and request.url.path.startswith("/api/"):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != API_TOKEN:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)


# =============================================================
# CONVERSATION SEARCH ENDPOINTS
# =============================================================

@app.get("/api/search")
def search_messages(
    q: str,
    limit: int = Query(default=20, le=100),
    type: str = Query(default=None, description="Filter by 'user' or 'assistant'"),
):
    """Full-text search over conversation messages with BM25 ranking."""
    conn = _get_conn()
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


@app.get("/api/similar-error")
def similar_error(
    error: str,
    limit: int = Query(default=5, le=20),
):
    """Find past errors and their solutions."""
    conn = _get_conn()
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
            "solution": solution["content"][:800] if solution else None,
        })

    return results


@app.get("/api/message/{uuid}")
def get_message(
    uuid: str,
    context: int = Query(default=1, ge=0, le=10),
):
    """Get full message content by UUID, with optional surrounding context."""
    conn = _get_conn()
    target = row_to_dict(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.role, m.content,
               m.timestamp, m.model, s.project
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.uuid = ?
    """, (uuid,)))

    if not target:
        return JSONResponse(status_code=404, content={"error": "message not found"})

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

    return {
        **target,
        "before": before[-context:],
        "after": after[:context],
    }


@app.get("/api/recent")
def recent_messages(
    hours: int = Query(default=24, le=168),
    limit: int = Query(default=50, le=200),
    session_id: str = Query(default=None),
):
    """Get recent messages, optionally filtered by session."""
    conn = _get_conn()
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
        # Compute cutoff in Python (portable across SQLite/Turso)
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


@app.get("/api/project/{project_name}")
def project_messages(
    project_name: str,
    limit: int = Query(default=20, le=100),
):
    """Get recent messages from a specific project."""
    conn = _get_conn()
    return rows_to_dicts(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.content, m.timestamp
        FROM messages m
        JOIN sessions s ON m.session_id = s.session_id
        WHERE s.project LIKE ?
        ORDER BY m.timestamp DESC
        LIMIT ?
    """, (f"%{project_name}%", limit)))


@app.get("/api/stats")
def stats():
    """Database statistics."""
    conn = _get_conn()
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    tool_count = conn.execute("SELECT COUNT(*) FROM tool_uses").fetchone()[0]
    vault_count = conn.execute("SELECT COUNT(*) FROM vault_docs").fetchone()[0]
    vault_link_count = conn.execute("SELECT COUNT(*) FROM vault_links").fetchone()[0]

    date_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM messages"
    ).fetchone()

    top_tools = rows_to_dicts(conn.execute("""
        SELECT tool_name, COUNT(*) as uses
        FROM tool_uses
        GROUP BY tool_name
        ORDER BY uses DESC
        LIMIT 10
    """))

    return {
        "messages": msg_count,
        "sessions": session_count,
        "tool_uses": tool_count,
        "vault_docs": vault_count,
        "vault_links": vault_link_count,
        "first_message": date_range[0],
        "last_message": date_range[1],
        "top_tools": top_tools,
    }


# =============================================================
# VAULT SEARCH ENDPOINTS
# =============================================================

@app.get("/api/vault/search")
def vault_search(
    q: str,
    project: str = Query(default=None),
    doc_type: str = Query(default=None),
    outcome: str = Query(default=None),
    limit: int = Query(default=10, le=50),
):
    """Faceted full-text search over curated vault content with weighted BM25."""
    conn = _get_conn()
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


@app.get("/api/vault/doc/{file_path:path}")
def vault_doc(file_path: str):
    """Get full vault document content by path."""
    conn = _get_conn()
    result = row_to_dict(conn.execute(
        "SELECT * FROM vault_docs WHERE file_path = ?", (file_path,)
    ))
    if not result:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return result


@app.get("/api/vault/stats")
def vault_stats():
    """Vault statistics."""
    conn = _get_conn()
    by_type = rows_to_dicts(conn.execute(
        "SELECT doc_type, COUNT(*) as count FROM vault_docs GROUP BY doc_type"
    ))
    by_project = rows_to_dicts(conn.execute(
        "SELECT project, COUNT(*) as count FROM vault_docs WHERE project IS NOT NULL GROUP BY project"
    ))
    by_outcome = rows_to_dicts(conn.execute(
        "SELECT outcome, COUNT(*) as count FROM vault_docs WHERE outcome IS NOT NULL GROUP BY outcome"
    ))
    link_count = conn.execute("SELECT COUNT(*) FROM vault_links").fetchone()[0]

    return {
        "total_docs": sum(r["count"] for r in by_type),
        "by_type": by_type,
        "by_project": by_project,
        "by_outcome": by_outcome,
        "total_links": link_count,
    }


@app.get("/api/vault/list")
def vault_list(
    project: str = Query(default=None),
    doc_type: str = Query(default=None),
    outcome: str = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """List vault documents with optional filters."""
    conn = _get_conn()
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
    content: str
    doc_type: str = "session"
    workflows_touched: List[str] = []


@app.post("/api/vault/archive")
def create_archive(archive: ArchiveCreate):
    """Create a new session archive directly in the vault_docs table."""
    conn = _get_conn()
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
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        conn.sync()

    return {
        "file_path": file_path,
        "status": "updated" if existing else "created",
    }


# =============================================================
# SYNC ENDPOINT
# =============================================================

@app.post("/api/sync")
def manual_sync():
    """Manually trigger embedded replica sync with Turso Cloud."""
    conn = _get_conn()
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return {"status": "skipped", "reason": "remote connection, no replica to sync"}
    conn.sync()
    return {"status": "synced"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
