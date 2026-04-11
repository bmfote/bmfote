"""FastMCP server — 5 memory tools for Claude Code, backed by Turso Cloud."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from engine.db import get_conn, rows_to_dicts, row_to_dict

# On Railway (or any cloud deploy), disable DNS rebinding protection
# since the bearer token already gates all access.
_on_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("PORT"))

mcp = FastMCP(
    "bmfote-memory",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=not _on_cloud,
    ) if not _on_cloud else None,
)


@mcp.tool()
def search_memory(query: str, limit: int = 20, type: Optional[str] = None) -> str:
    """Full-text search over all conversation messages.

    Args:
        query: FTS5 search query (supports AND, OR, NOT, "phrase", prefix*)
        limit: Max results (default 20, max 100)
        type: Filter by message type — 'user' or 'assistant'
    """
    limit = min(limit, 100)
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
    params: list = [query]

    if type:
        sql += " AND m.type = ?"
        params.append(type)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = rows_to_dicts(conn.execute(sql, tuple(params)))

    if not results:
        return f"No results for: {query}"

    lines = [f"Found {len(results)} results for: {query}\n"]
    for r in results:
        project = r.get("project") or "unknown"
        lines.append(
            f"- [{r['type']}] {r['snippet']}\n"
            f"  project={project}  ts={r['timestamp']}  uuid={r['uuid']}"
        )
    return "\n".join(lines)


@mcp.tool()
def find_error(error_text: str, limit: int = 5) -> str:
    """Find past errors and the solutions that followed them.

    Args:
        error_text: Error message or keywords to search for
        limit: Max results (default 5, max 20)
    """
    limit = min(limit, 20)
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
    """, (error_text, limit)))

    if not error_matches:
        return f"No past errors matching: {error_text}"

    lines = [f"Found {len(error_matches)} past error(s) matching: {error_text}\n"]
    for err in error_matches:
        solution = row_to_dict(conn.execute("""
            SELECT content, timestamp
            FROM messages
            WHERE parent_uuid = ? AND type = 'assistant'
            LIMIT 1
        """, (err["uuid"],)))

        project = err.get("project") or "unknown"
        error_preview = (err["content"] or "")[:600]
        lines.append(f"--- Error ({project}, {err['timestamp']}) ---")
        lines.append(error_preview)

        if solution:
            solution_preview = (solution["content"] or "")[:600]
            lines.append(f"\n  Solution:")
            lines.append(f"  {solution_preview}")
        else:
            lines.append("  (no solution found)")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def get_context(uuid: str, context: int = 1) -> str:
    """Get a full message by UUID with surrounding conversation context.

    Args:
        uuid: Message UUID (from search results)
        context: Number of messages before/after to include (0-10, default 1)
    """
    context = max(0, min(context, 10))
    conn = get_conn()

    target = row_to_dict(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.role, m.content,
               m.timestamp, m.model, s.project
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.uuid = ?
    """, (uuid,)))

    if not target:
        return f"Message not found: {uuid}"

    lines = []
    project = target.get("project") or "unknown"
    lines.append(f"Message {uuid} — project={project}, {target['type']}, {target['timestamp']}\n")

    if context > 0:
        context_rows = rows_to_dicts(conn.execute("""
            SELECT uuid, type, role, content, timestamp, model
            FROM messages
            WHERE session_id = ? AND uuid != ?
            ORDER BY timestamp
        """, (target["session_id"], uuid)))

        before = [m for m in context_rows if m["timestamp"] <= target["timestamp"]]
        after = [m for m in context_rows if m["timestamp"] > target["timestamp"]]

        for m in before[-context:]:
            lines.append(f"[before] [{m['type']}] {(m['content'] or '')[:400]}")
        lines.append("")

    lines.append(f">>> [{target['type']}] {target['content'] or ''}")

    if context > 0:
        lines.append("")
        for m in after[:context]:
            lines.append(f"[after] [{m['type']}] {(m['content'] or '')[:400]}")

    return "\n".join(lines)


@mcp.tool()
def get_recent(hours: int = 24, limit: int = 50) -> str:
    """Get recent conversation messages — what was I working on?

    Args:
        hours: How far back to look (default 24, max 168)
        limit: Max results (default 50, max 200)
    """
    hours = min(hours, 168)
    limit = min(limit, 200)
    conn = get_conn()

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    results = rows_to_dicts(conn.execute("""
        SELECT m.uuid, m.session_id, m.type, m.role, m.content, m.timestamp,
               s.project
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.timestamp > ?
        ORDER BY m.timestamp DESC LIMIT ?
    """, (cutoff, limit)))

    if not results:
        return f"No messages in the last {hours} hours."

    lines = [f"{len(results)} messages in the last {hours} hours:\n"]
    for r in results:
        project = r.get("project") or "unknown"
        preview = (r["content"] or "")[:120].replace("\n", " ")
        lines.append(f"- [{r['type']}] {preview}  (project={project}, {r['timestamp']})")

    return "\n".join(lines)


@mcp.tool()
def search_vault(query: str, project: Optional[str] = None, limit: int = 10) -> str:
    """Search the curated knowledge base (session archives, notes, docs).

    Args:
        query: FTS5 search query
        project: Filter by project name (optional)
        limit: Max results (default 10, max 50)
    """
    limit = min(limit, 50)
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
    params: list = [query]

    if project:
        sql += " AND v.project = ?"
        params.append(project)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = rows_to_dicts(conn.execute(sql, tuple(params)))

    if not results:
        return f"No vault docs matching: {query}"

    lines = [f"Found {len(results)} vault docs for: {query}\n"]
    for r in results:
        outcome = r.get("outcome") or ""
        tags = r.get("tags") or ""
        lines.append(
            f"- [{r['doc_type']}] {r['topic']}\n"
            f"  project={r['project']}  date={r['date']}  outcome={outcome}\n"
            f"  tags={tags}\n"
            f"  {r['snippet']}\n"
            f"  path={r['file_path']}"
        )

    return "\n".join(lines)
