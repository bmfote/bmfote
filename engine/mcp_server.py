"""FastMCP server — memory tools for Claude Code and Managed Agents.

Exposes 4 read tools (search_memory, find_error, get_context, get_recent)
plus 1 write tool (remember) so agents that can't use client-side hooks —
Anthropic Managed Agents in particular — can persist their own findings
into the same memory store other runtimes read from.
"""

import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from engine.db import get_conn, is_remote_db

# In remote-DB mode, disable DNS rebinding protection — bearer token gates access.
# NOTE: passing transport_security=None falls back to defaults which ENABLE
# protection with only localhost allowed, so we must pass explicit settings.
_on_cloud = is_remote_db()

mcp = FastMCP(
    "cctx-memory",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=not _on_cloud,
    ),
)


def _get_queries():
    """Late import to avoid circular dependency (server.py imports mcp from here)."""
    from engine.server import (
        query_search, query_similar_error, query_message, query_recent,
    )
    return query_search, query_similar_error, query_message, query_recent


@mcp.tool()
def search_memory(
    query: str,
    limit: int = 20,
    type: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Full-text search over conversation messages in a workspace.

    Args:
        query: FTS5 search query (supports AND, OR, NOT, "phrase", prefix*)
        limit: Max results (default 20, max 100)
        type: Filter by message type — 'user' or 'assistant'
        workspace: Workspace scope (defaults to 'cctx-default'). Memories in
            different workspaces are fully isolated.
    """
    q_search, *_ = _get_queries()
    limit = min(limit, 100)

    try:
        results = q_search(query, limit, type, workspace)
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
def find_error(error_text: str, limit: int = 5, workspace: Optional[str] = None) -> str:
    """Find past errors and the solutions that followed them.

    Args:
        error_text: Error message or keywords to search for
        limit: Max results (default 5, max 20)
        workspace: Workspace scope (defaults to 'cctx-default').
    """
    _, q_error, *_ = _get_queries()
    limit = min(limit, 20)

    try:
        results = q_error(error_text, limit, workspace)
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
def get_context(uuid: str, context: int = 1, workspace: Optional[str] = None) -> str:
    """Get a full message by UUID with surrounding conversation context.

    Args:
        uuid: Message UUID (from search results)
        context: Number of messages before/after to include (0-10, default 1)
        workspace: Workspace scope (defaults to 'cctx-default'). A UUID in
            another workspace returns 'not found' even if guessed correctly.
    """
    _, _, q_message, *_ = _get_queries()
    context = max(0, min(context, 10))

    result = q_message(uuid, context, workspace)
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
def get_recent(hours: int = 24, limit: int = 50, workspace: Optional[str] = None) -> str:
    """Get recent conversation messages — what was I working on?

    Args:
        hours: How far back to look (default 24, max 168)
        limit: Max results (default 50, max 200)
        workspace: Workspace scope (defaults to 'cctx-default').
    """
    *_, q_recent = _get_queries()
    hours = min(hours, 168)
    limit = min(limit, 200)

    results = q_recent(hours, limit, None, workspace)

    if not results:
        return f"No messages in the last {hours} hours."

    lines = [f"{len(results)} messages in the last {hours} hours:\n"]
    for r in results:
        project = r.get("project") or "unknown"
        preview = (r["content"] or "")[:120].replace("\n", " ")
        lines.append(f"- [{r['type']}] {preview}  (project={project}, {r['timestamp']})")

    return "\n".join(lines)


@mcp.tool()
def remember(
    content: str,
    topic: str = "",
    project: str = "managed-agent",
    workspace: Optional[str] = None,
) -> str:
    """Save something to long-term memory for future agent sessions to recall.

    Writes into the same conversation store `search_memory` reads from, so a
    later agent run — your next session, or a different agent scoped to the
    same workspace — will surface this via a normal recall query.

    Use this when you want future runs to build on what you just learned.
    Good for: research findings, decisions made, facts discovered, contacts
    gathered, short summaries of completed work. Bad for: the entire turn
    transcript (the orchestrator captures that separately).

    Args:
        content: The text to persist. Be self-contained and specific — a
            future reader must understand it without seeing this conversation.
        topic: Short title (under 80 chars), prefixed to content for
            searchability. Optional.
        project: Project scope — a human-readable label on the session row.
            Defaults to "managed-agent".
        workspace: Workspace scope — the hard isolation boundary. Memories in
            different workspaces never cross over in recall. Defaults to
            'cctx-default'.
    """
    if not content or not content.strip():
        return "Nothing to remember — content was empty."

    conn = get_conn()
    ws = workspace or "cctx-default"
    session_id = f"agent-memory-{project}"
    now = datetime.now(timezone.utc).isoformat()

    # Upsert the session row so the messages.session_id FK resolves.
    conn.execute(
        """
        INSERT INTO sessions (session_id, project, first_message_at, last_message_at, message_count)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(session_id) DO UPDATE SET
            last_message_at = excluded.last_message_at,
            project = COALESCE(sessions.project, excluded.project)
        """,
        (session_id, project, now, now),
    )

    # Write the memory as an assistant message so it's homogeneous with the
    # rest of cctx's data and searchable via search_memory / get_recent.
    msg_uuid = str(_uuid.uuid4())
    topic_clean = (topic or "").strip()
    body = f"[{topic_clean}]\n{content}" if topic_clean else content
    body = body[:50_000]

    conn.execute(
        """
        INSERT INTO messages (uuid, session_id, type, role, content, timestamp, workspace_id)
        VALUES (?, ?, 'assistant', 'assistant', ?, ?, ?)
        """,
        (msg_uuid, session_id, body, now, ws),
    )

    conn.commit()
    if not is_remote_db():
        conn.sync()

    return (
        f"Memory saved to workspace '{ws}' (project='{project}'). "
        f"Searchable via search_memory. uuid={msg_uuid}"
    )
