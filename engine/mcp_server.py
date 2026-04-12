"""FastMCP server — 5 memory tools for Claude Code, backed by shared query functions."""

from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from engine.db import is_remote_db

# In remote-DB mode, disable DNS rebinding protection — bearer token gates access.
# NOTE: passing transport_security=None falls back to defaults which ENABLE
# protection with only localhost allowed, so we must pass explicit settings.
_on_cloud = is_remote_db()

mcp = FastMCP(
    "bmfote-memory",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=not _on_cloud,
    ),
)


def _get_queries():
    """Late import to avoid circular dependency (server.py imports mcp from here)."""
    from engine.server import (
        query_search, query_similar_error, query_message,
        query_recent, query_vault_search,
    )
    return query_search, query_similar_error, query_message, query_recent, query_vault_search


@mcp.tool()
def search_memory(query: str, limit: int = 20, type: Optional[str] = None) -> str:
    """Full-text search over all conversation messages.

    Args:
        query: FTS5 search query (supports AND, OR, NOT, "phrase", prefix*)
        limit: Max results (default 20, max 100)
        type: Filter by message type — 'user' or 'assistant'
    """
    q_search, *_ = _get_queries()
    limit = min(limit, 100)

    try:
        results = q_search(query, limit, type)
    except Exception:
        return f"Invalid search query: {query}"

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
    _, q_error, *_ = _get_queries()
    limit = min(limit, 20)

    try:
        results = q_error(error_text, limit)
    except Exception:
        return f"Invalid search query: {error_text}"

    if not results:
        return f"No past errors matching: {error_text}"

    lines = [f"Found {len(results)} past error(s) matching: {error_text}\n"]
    for err in results:
        project = err.get("project") or "unknown"
        error_preview = (err["error_context"] or "")[:600]
        lines.append(f"--- Error ({project}, {err['timestamp']}) ---")
        lines.append(error_preview)

        if err.get("solution"):
            lines.append(f"\n  Solution:")
            lines.append(f"  {err['solution'][:600]}")
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
    _, _, q_message, *_ = _get_queries()
    context = max(0, min(context, 10))

    result = q_message(uuid, context)
    if not result:
        return f"Message not found: {uuid}"

    project = result.get("project") or "unknown"
    lines = [f"Message {uuid} — project={project}, {result['type']}, {result['timestamp']}\n"]

    for m in result.get("before", []):
        lines.append(f"[before] [{m['type']}] {(m['content'] or '')[:400]}")
    if result.get("before"):
        lines.append("")

    lines.append(f">>> [{result['type']}] {result['content'] or ''}")

    if result.get("after"):
        lines.append("")
        for m in result["after"]:
            lines.append(f"[after] [{m['type']}] {(m['content'] or '')[:400]}")

    return "\n".join(lines)


@mcp.tool()
def get_recent(hours: int = 24, limit: int = 50) -> str:
    """Get recent conversation messages — what was I working on?

    Args:
        hours: How far back to look (default 24, max 168)
        limit: Max results (default 50, max 200)
    """
    *_, q_recent, _ = _get_queries()
    hours = min(hours, 168)
    limit = min(limit, 200)

    results = q_recent(hours, limit)

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
    *_, q_vault = _get_queries()
    limit = min(limit, 50)

    try:
        results = q_vault(query, project, limit=limit)
    except Exception:
        return f"Invalid search query: {query}"

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
