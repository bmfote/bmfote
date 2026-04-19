"""FastMCP server — cloud context tools for Claude Code and Managed Agents.

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


def _get_def_queries():
    """Late import — same circular-dep workaround."""
    from engine.server import (
        query_propose_edit,
        query_pending_edits,
        query_apply_edit,
        query_reject_edit,
        query_edit_history,
    )
    return (
        query_propose_edit,
        query_pending_edits,
        query_apply_edit,
        query_reject_edit,
        query_edit_history,
    )


@mcp.tool()
def search_memory(
    query: str,
    limit: int = 20,
    type: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Full-text search over cloud context — all conversation messages across every agent surface. FTS5, <100ms.

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
def get_recent(
    hours: int = 24,
    limit: int = 50,
    workspace: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Get recent cloud context — what was I (or any agent) working on?

    Args:
        hours: How far back to look (default 24, max 168). Ignored when session_id is set.
        limit: Max results (default 50, max 200)
        workspace: Workspace scope (defaults to 'cctx-default').
        session_id: When set, returns messages from that specific session only —
            used by the session-start recap to pull a prior session's content.
    """
    *_, q_recent = _get_queries()
    hours = min(hours, 168)
    limit = min(limit, 200)

    results = q_recent(hours, limit, session_id, workspace)

    if not results:
        scope = f"session {session_id}" if session_id else f"the last {hours} hours"
        return f"No messages in {scope}."

    header = (
        f"{len(results)} messages from session {session_id}:\n"
        if session_id
        else f"{len(results)} messages in the last {hours} hours:\n"
    )
    lines = [header]
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
    """Save something to cloud context for any future agent session to recall.

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


# =============================================================
# DEFINITION EDITS — canonical doc edits with session provenance
# =============================================================


@mcp.tool()
def propose_definition_edit(
    file_path: str,
    new_content: str,
    source_session_id: str,
    old_content: Optional[str] = None,
    reason: Optional[str] = None,
    confidence: Optional[float] = None,
    source_message_uuid: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Queue a proposed edit to a canonical project definition file (icp.md,
    playbook.md, etc.) for human review. The edit lands in the user's review
    queue, linked by session_id for provenance. Local files remain the source
    of truth — this only records the AI-proposed-edit history.

    Use when the current session has refined a tracked definition with concrete
    new information. Bias heavily toward proposing nothing if the change is
    speculative.

    Args:
        file_path: Relative path from project root (e.g., 'icp.md').
        new_content: Proposed new file content after the edit.
        source_session_id: The session_id that produced this edit.
        old_content: Current file content before the edit. Optional but
            strongly recommended so the user sees a real diff.
        reason: One-sentence justification for the edit. Cite the transcript.
        confidence: 0-1 score. Edits below 0.7 are filtered by the proposer.
        source_message_uuid: Specific message that triggered the edit, for
            deep-linking back into the source session.
        workspace: Workspace scope. Defaults to 'cctx-default'.
    """
    q_propose, *_ = _get_def_queries()
    ws = workspace or "cctx-default"
    edit_uuid = str(_uuid.uuid4())
    result = q_propose(
        uuid=edit_uuid,
        workspace_id=ws,
        file_path=file_path,
        new_content=new_content,
        old_content=old_content,
        reason=reason,
        confidence=confidence,
        source_session_id=source_session_id,
        source_message_uuid=source_message_uuid,
    )
    return (
        f"Proposed edit queued for {file_path} in workspace '{ws}'. "
        f"uuid={result['uuid']} — run `cctx review` to approve or reject."
    )


@mcp.tool()
def list_pending_definition_edits(
    workspace: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List pending definition edits in the review queue for this workspace.

    Args:
        workspace: Workspace scope. Defaults to 'cctx-default'.
        limit: Max entries (default 50, max 200).
    """
    _, q_pending, *_ = _get_def_queries()
    limit = min(limit, 200)
    results = q_pending(workspace, limit)
    if not results:
        return f"No pending definition edits in workspace '{workspace or 'cctx-default'}'."

    lines = [f"{len(results)} pending edit(s):\n"]
    for r in results:
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "?"
        reason = (r.get("reason") or "")[:120]
        lines.append(
            f"- {r['file_path']} (conf={conf}) — {reason}\n"
            f"  uuid={r['uuid']}  session={r['source_session_id']}  created={r['created_at']}"
        )
    return "\n".join(lines)


@mcp.tool()
def apply_definition_edit(edit_uuid: str, workspace: Optional[str] = None) -> str:
    """Approve a pending definition edit. The DB row flips to 'approved'; the
    caller is responsible for writing new_content to disk (the CLI does this).

    Args:
        edit_uuid: UUID of the pending edit.
        workspace: Workspace scope. Defaults to 'cctx-default'.
    """
    _, _, q_apply, *_ = _get_def_queries()
    result = q_apply(edit_uuid, workspace)
    if result is None:
        return f"Edit not found: {edit_uuid}"
    return (
        f"Edit {edit_uuid} approved for {result['file_path']}. "
        f"Write new_content to disk to complete the apply."
    )


@mcp.tool()
def reject_definition_edit(edit_uuid: str, workspace: Optional[str] = None) -> str:
    """Reject a pending definition edit.

    Args:
        edit_uuid: UUID of the pending edit.
        workspace: Workspace scope. Defaults to 'cctx-default'.
    """
    _, _, _, q_reject, _ = _get_def_queries()
    result = q_reject(edit_uuid, workspace)
    if result is None:
        return f"Edit not found: {edit_uuid}"
    return f"Edit {edit_uuid} rejected for {result['file_path']}."


@mcp.tool()
def search_definition_history(
    file_path: Optional[str] = None,
    workspace: Optional[str] = None,
    limit: int = 50,
) -> str:
    """Audit trail of proposed/approved/rejected edits for a file (or all
    files in the workspace if file_path is omitted). Most-recent first.

    Use when the user asks "why did we change X?" or "when did we update
    our ICP?" — surfaces the session that produced each historical edit.

    Args:
        file_path: Filter to a specific file (e.g., 'icp.md'). Optional.
        workspace: Workspace scope. Defaults to 'cctx-default'.
        limit: Max entries (default 50, max 200).
    """
    *_, q_history = _get_def_queries()
    limit = min(limit, 200)
    results = q_history(workspace, file_path, limit)
    if not results:
        scope = f"file '{file_path}'" if file_path else f"workspace '{workspace or 'cctx-default'}'"
        return f"No definition edit history for {scope}."

    lines = [f"{len(results)} edit(s):\n"]
    for r in results:
        reason = (r.get("reason") or "")[:120]
        reviewed = r.get("reviewed_at") or "pending"
        lines.append(
            f"- [{r['status']}] {r['file_path']} — {reason}\n"
            f"  session={r['source_session_id']}  created={r['created_at']}  reviewed={reviewed}"
        )
    return "\n".join(lines)
