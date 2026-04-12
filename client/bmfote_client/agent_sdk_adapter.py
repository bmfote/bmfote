"""Claude Agent SDK adapter — plug bmfote into a Claude Agent SDK run.

Usage:
    from claude_agent_sdk import ClaudeAgentOptions, query
    from bmfote_client import agent_sdk_hooks

    options = ClaudeAgentOptions(hooks=agent_sdk_hooks(project="research-agent"))
    async for msg in query(prompt="...", options=options):
        ...

Captures:
  UserPromptSubmit → Session.record_user
  PostToolUse      → Session.record_assistant with [tool_use: name] + [tool_result] + truncated
  Stop             → Session.close

Not captured: the assistant's text response between tool calls. The Agent SDK
does not expose a hook that fires on plain assistant messages. For that,
post-process the transcript file at input_data["transcript_path"] (same JSONL
format the existing engine/sync_conversations.py already handles).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .client import Session

_TOOL_RESULT_CAP = 500


def _session_key(input_data: Any) -> str:
    if isinstance(input_data, dict):
        return input_data.get("session_id") or "default"
    return getattr(input_data, "session_id", None) or "default"


def _get(input_data: Any, key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict):
        return input_data.get(key, default)
    return getattr(input_data, key, default)


def _truncate_tool_response(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response[:_TOOL_RESULT_CAP]
    if isinstance(response, list):
        parts = []
        for block in response:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append((block.get("text") or "")[:_TOOL_RESULT_CAP])
        if parts:
            return "\n".join(parts)
    return str(response)[:_TOOL_RESULT_CAP]


def agent_sdk_hooks(
    project: str,
    client: Optional[Any] = None,
) -> Dict[str, list]:
    """Return a hooks dict suitable for ClaudeAgentOptions(hooks=...).

    `client` may be a bmfote_client.Client instance. If omitted, a default one
    is created from BMFOTE_URL / BMFOTE_TOKEN env vars on first use.
    """
    try:
        from claude_agent_sdk import HookMatcher
    except ImportError as e:
        raise ImportError(
            "claude-agent-sdk is not installed. "
            "Install it with: pip install claude-agent-sdk"
        ) from e

    from .client import Client

    _client_holder: Dict[str, Any] = {"client": client}
    sessions: Dict[str, Session] = {}

    def _get_client() -> Client:
        if _client_holder["client"] is None:
            _client_holder["client"] = Client()
        return _client_holder["client"]

    def _get_session(session_id: str) -> Session:
        if session_id not in sessions:
            sessions[session_id] = _get_client().session(
                project=project, session_id=session_id
            )
        return sessions[session_id]

    async def on_user_prompt(input_data, tool_use_id, context):
        try:
            sess = _get_session(_session_key(input_data))
            prompt = _get(input_data, "prompt", "") or ""
            sess.record_user(prompt)
        except Exception:
            pass
        return {}

    async def on_post_tool_use(input_data, tool_use_id, context):
        try:
            sess = _get_session(_session_key(input_data))
            tool_name = _get(input_data, "tool_name", "unknown")
            tool_response = _get(input_data, "tool_response")
            content = (
                f"[tool_use: {tool_name}]\n"
                f"[tool_result]\n{_truncate_tool_response(tool_response)}"
            )
            sess.record_assistant(content)
        except Exception:
            pass
        return {}

    async def on_stop(input_data, tool_use_id, context):
        try:
            sid = _session_key(input_data)
            sess = sessions.pop(sid, None)
            if sess is not None:
                sess.close()
        except Exception:
            pass
        return {}

    return {
        "UserPromptSubmit": [HookMatcher(hooks=[on_user_prompt])],
        "PostToolUse": [HookMatcher(hooks=[on_post_tool_use])],
        "Stop": [HookMatcher(hooks=[on_stop])],
    }
