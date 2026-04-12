"""Anthropic tool specs for the bmfote memory API.

Drop `TOOL_SPECS` into `anthropic.Anthropic().messages.create(tools=...)` and
dispatch tool_use blocks through `handle_tool_use(block, client)` to let your
agent recall its own experiential memory during a turn.

Formatting of the returned tool_result strings mirrors engine/mcp_server.py so
Messages API agents get the same shape an MCP-native client would see.
"""

from __future__ import annotations

from typing import Any, List

from .client import Client, _strip_fts_markers


TOOL_SPECS: List[dict] = [
    {
        "name": "search_memory",
        "description": (
            "Full-text search over prior conversation messages (experiential memory). "
            "Use this to recall what you or a previous agent session already did, said, or discovered. "
            "Supports FTS5 syntax: AND, OR, NOT, \"exact phrase\", prefix*."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "integer", "description": "Max results (default 10, max 50)"},
                "type": {"type": "string", "description": "Filter by 'user' or 'assistant' (optional)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_error",
        "description": (
            "Find past errors and the assistant response that followed them. "
            "Use this when you hit an error to see if it's been solved before."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "error_text": {"type": "string", "description": "Error message or keywords"},
                "limit": {"type": "integer", "description": "Max results (default 5, max 20)"},
            },
            "required": ["error_text"],
        },
    },
    {
        "name": "get_context",
        "description": (
            "Fetch a full message by UUID with surrounding conversation context. "
            "Use this to expand a search_memory hit into the full exchange it came from."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string", "description": "Message UUID (from search_memory)"},
                "context": {"type": "integer", "description": "Messages before/after to include (0-10, default 1)"},
            },
            "required": ["uuid"],
        },
    },
    {
        "name": "get_recent",
        "description": (
            "Get recent messages across all sessions — what was recently worked on?"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "How far back to look (default 24, max 168)"},
                "limit": {"type": "integer", "description": "Max results (default 20, max 100)"},
            },
        },
    },
    {
        "name": "search_vault",
        "description": (
            "Search the curated knowledge base (session archives, notes, docs). "
            "Different from search_memory — vault docs are hand-curated summaries, not raw turns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "project": {"type": "string", "description": "Filter by project (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 5, max 20)"},
            },
            "required": ["query"],
        },
    },
]


# =============================================================
# Formatters — match engine/mcp_server.py output shape
# =============================================================


def _fmt_search(query: str, results: List[dict]) -> str:
    if not results:
        return f"No results for: {query}"
    lines = [f"Found {len(results)} results for: {query}", ""]
    for r in results:
        snippet = _strip_fts_markers(r.get("snippet") or "")
        project = r.get("project") or "unknown"
        lines.append(
            f"- [{r.get('type','msg')}] {snippet}\n"
            f"  project={project}  ts={r.get('timestamp','')}  uuid={r.get('uuid','')}"
        )
    return "\n".join(lines)


def _fmt_find_error(error_text: str, results: List[dict]) -> str:
    if not results:
        return f"No past errors matching: {error_text}"
    lines = [f"Found {len(results)} past error(s) matching: {error_text}", ""]
    for err in results:
        project = err.get("project") or "unknown"
        preview = (err.get("error_context") or "")[:600]
        lines.append(f"--- Error ({project}, {err.get('timestamp','')}) ---")
        lines.append(preview)
        solution = err.get("solution")
        if solution:
            lines.append("\n  Solution:")
            lines.append(f"  {solution[:600]}")
        else:
            lines.append("  (no solution found)")
        lines.append("")
    return "\n".join(lines)


def _fmt_get_context(uuid: str, result: Any) -> str:
    if not result:
        return f"Message not found: {uuid}"
    project = result.get("project") or "unknown"
    lines = [
        f"Message {uuid} — project={project}, {result.get('type','msg')}, {result.get('timestamp','')}",
        "",
    ]
    for m in result.get("before", []):
        lines.append(f"[before] [{m.get('type','msg')}] {(m.get('content') or '')[:400]}")
    if result.get("before"):
        lines.append("")
    lines.append(f">>> [{result.get('type','msg')}] {result.get('content') or ''}")
    if result.get("after"):
        lines.append("")
        for m in result["after"]:
            lines.append(f"[after] [{m.get('type','msg')}] {(m.get('content') or '')[:400]}")
    return "\n".join(lines)


def _fmt_recent(hours: int, results: List[dict]) -> str:
    if not results:
        return f"No messages in the last {hours} hours."
    lines = [f"{len(results)} messages in the last {hours} hours:", ""]
    for r in results:
        project = r.get("project") or "unknown"
        preview = (r.get("content") or "")[:120].replace("\n", " ")
        lines.append(f"- [{r.get('type','msg')}] {preview}  (project={project}, {r.get('timestamp','')})")
    return "\n".join(lines)


def _fmt_vault(query: str, results: List[dict]) -> str:
    if not results:
        return f"No vault docs matching: {query}"
    lines = [f"Found {len(results)} vault docs for: {query}", ""]
    for r in results:
        outcome = r.get("outcome") or ""
        tags = r.get("tags") or ""
        snippet = _strip_fts_markers(r.get("snippet") or "")
        lines.append(
            f"- [{r.get('doc_type','doc')}] {r.get('topic','')}\n"
            f"  project={r.get('project','')}  date={r.get('date','')}  outcome={outcome}\n"
            f"  tags={tags}\n"
            f"  {snippet}\n"
            f"  path={r.get('file_path','')}"
        )
    return "\n".join(lines)


# =============================================================
# Dispatcher
# =============================================================


def _tool_name_and_input(block: Any):
    """Duck-type extraction of (name, input_dict) from an Anthropic ToolUseBlock."""
    if isinstance(block, dict):
        return block.get("name"), block.get("input") or {}
    return getattr(block, "name", None), getattr(block, "input", None) or {}


def handle_tool_use(block: Any, client: Client) -> str:
    """Dispatch an Anthropic tool_use block to the right bmfote read method.

    Returns a string suitable for the next messages.create call's tool_result:
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.id, "content": result_str}]}

    Unknown tool names return a short error string rather than raising, so a
    misrouted tool_use doesn't crash the agent loop.
    """
    name, inp = _tool_name_and_input(block)

    if name == "search_memory":
        query = inp.get("query", "")
        return _fmt_search(
            query,
            client.search(query, limit=min(int(inp.get("limit", 10)), 50), type=inp.get("type")),
        )

    if name == "find_error":
        err = inp.get("error_text", "")
        return _fmt_find_error(
            err,
            client.find_error(err, limit=min(int(inp.get("limit", 5)), 20)),
        )

    if name == "get_context":
        uuid = inp.get("uuid", "")
        return _fmt_get_context(
            uuid,
            client.get_message(uuid, context=max(0, min(int(inp.get("context", 1)), 10))),
        )

    if name == "get_recent":
        hours = min(int(inp.get("hours", 24)), 168)
        return _fmt_recent(
            hours,
            client.recent(hours=hours, limit=min(int(inp.get("limit", 20)), 100)),
        )

    if name == "search_vault":
        query = inp.get("query", "")
        return _fmt_vault(
            query,
            client.vault_search(
                query,
                project=inp.get("project"),
                limit=min(int(inp.get("limit", 5)), 20),
            ),
        )

    return f"Unknown bmfote tool: {name}"
