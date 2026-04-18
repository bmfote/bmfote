"""
Shared helper for calling the claude CLI with structured output.

Why CLI instead of Anthropic SDK:
- Uses the user's existing Claude Code OAuth (no ANTHROPIC_API_KEY required)
- Bills against the Claude Code subscription, not the API
- Same API-side prompt cache hit rate (ephemeral 1h by default via CLI)

Why we bypass `claude` on PATH:
- The PATH `claude` is typically a cmux shim at
  /Applications/cmux.app/Contents/Resources/bin/claude that injects
  --session-id and --settings with cmux hooks. That causes recursive hangs
  when called from inside a Claude Code session.
- We call the real binary directly at REAL_CLAUDE_BINARY, combined with
  `--setting-sources ""` to skip all inherited settings.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

REAL_CLAUDE_BINARY = "/Users/mattbatterson/.local/bin/claude"

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_TIMEOUT_S = 180


class CLIError(RuntimeError):
    """Raised when the CLI subprocess fails or returns no structured output."""


def call_structured(
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """
    Make one structured-output call via the claude CLI.

    Returns the `structured_output` field from the CLI envelope on success,
    with two added keys:
      _elapsed_s: wall-clock subprocess time
      _usage: dict with cache_read / cache_creation / output token counts

    Raises CLIError on non-zero exit, timeout, missing structured_output,
    or envelope parse failure.
    """
    if not Path(REAL_CLAUDE_BINARY).exists():
        raise CLIError(
            f"real claude binary not found at {REAL_CLAUDE_BINARY}. "
            "Is Claude Code installed?"
        )

    cmd = [
        REAL_CLAUDE_BINARY,
        "--print",
        "--model", model,
        "--no-session-persistence",
        "--setting-sources", "",
        "--tools", "",
        "--permission-mode", "default",
        "--system-prompt", system_prompt,
        "--json-schema", json.dumps(schema),
        "--output-format", "json",
        user_prompt,
    ]

    started = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CLIError(f"claude CLI timed out after {timeout_s}s") from e

    elapsed = time.time() - started

    if result.returncode != 0:
        raise CLIError(
            f"claude CLI exit {result.returncode} after {elapsed:.1f}s\n"
            f"stderr: {result.stderr[:1000]}\n"
            f"stdout: {result.stdout[:500]}"
        )

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise CLIError(
            f"claude CLI envelope not valid JSON: {e}\n"
            f"stdout head: {result.stdout[:500]}"
        ) from e

    if envelope.get("is_error"):
        raise CLIError(
            f"claude CLI returned error envelope: "
            f"subtype={envelope.get('subtype')} "
            f"result={envelope.get('result', '')[:500]}"
        )

    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        raise CLIError(
            "claude CLI envelope missing structured_output field. "
            f"Keys: {list(envelope.keys())}. "
            f"Result text: {envelope.get('result', '')[:500]}"
        )

    usage = envelope.get("usage", {})
    structured["_elapsed_s"] = round(elapsed, 2)
    structured["_usage"] = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
    }
    structured["_duration_ms"] = envelope.get("duration_ms", 0)
    return structured
