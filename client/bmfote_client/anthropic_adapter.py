"""Anthropic Messages API adapter — one call per agent turn.

Flattening rules mirror engine/sync_conversations.py:25-49 so API-agent writes
land in the same shape as Claude Code sessions already do.
"""

from __future__ import annotations

from typing import Any, Union

from .client import Session

_TOOL_RESULT_CAP = 500


def _field(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _flatten_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content[:_TOOL_RESULT_CAP]
    if isinstance(content, list):
        parts = []
        for sub in content:
            if _field(sub, "type") == "text":
                parts.append((_field(sub, "text") or "")[:_TOOL_RESULT_CAP])
        return "\n".join(parts)
    return str(content or "")[:_TOOL_RESULT_CAP]


def _flatten_blocks(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return str(blocks or "")
    parts = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue
        btype = _field(block, "type")
        if btype == "text":
            parts.append(_field(block, "text") or "")
        elif btype == "tool_use":
            name = _field(block, "name") or "unknown"
            parts.append(f"[tool_use: {name}]")
        elif btype == "tool_result":
            parts.append(f"[tool_result]\n{_flatten_tool_result_content(_field(block, 'content'))}")
    return "\n".join(parts)


def _flatten_user_message(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict) and "content" in msg:
        return _flatten_blocks(msg["content"])
    return _flatten_blocks(msg)


def _flatten_assistant_response(response: Any) -> str:
    content = _field(response, "content")
    return _flatten_blocks(content)


def _extract_usage(response: Any) -> Union[dict, None]:
    usage = _field(response, "usage")
    if usage is None:
        return None
    return {
        "input_tokens": _field(usage, "input_tokens"),
        "output_tokens": _field(usage, "output_tokens"),
    }


def record_exchange(session: Session, user_message: Any, assistant_response: Any) -> None:
    """Record one (user, assistant) pair from a Messages API call.

    user_message: str, list of content blocks, or a dict {"role": ..., "content": ...}.
    assistant_response: anthropic.types.Message (duck-typed on .content/.model/.usage).
    """
    session.record_user(_flatten_user_message(user_message))
    session.record_assistant(
        _flatten_assistant_response(assistant_response),
        model=_field(assistant_response, "model"),
        usage=_extract_usage(assistant_response),
    )
