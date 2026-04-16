"""
Moat-track candidate proposer + code-track patch proposer.
Uses the claude CLI via cli_client.py so everything routes through
Claude Code OAuth. Zero API-key requirement.

The agent assembles a system prompt from program.md + ground-truth
posts + the rubric. The user prompt contains the mode, recent survivors
(for anti-repetition), and optional persona hint. Output is a structured
candidate dict via --json-schema.
"""

from __future__ import annotations

from typing import Any

from autoresearch.cli_client import CLIError, call_structured
from autoresearch.prepare import (
    CODE_DIR,
    CODE_GROUND_TRUTH,
    CODE_RUBRIC,
    MOAT_DIR,
    MOAT_GROUND_TRUTH,
    MOAT_RUBRIC,
    REPO_ROOT,
)

DEFAULT_MODEL = "claude-sonnet-4-5"

PROPOSE_CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "mode",
        "persona",
        "channel",
        "counter_target",
        "contradiction",
        "why",
        "how",
        "what",
    ],
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["refine", "discover"],
            "description": "Must match the mode you were told to use in the user prompt.",
        },
        "persona": {
            "type": "string",
            "description": "Specific job title + company profile. Not 'teams' or 'developers'.",
        },
        "channel": {
            "type": "string",
            "description": "Which of the 4 channels (SMB / dev-first / agencies / fractional).",
        },
        "counter_target": {
            "type": "string",
            "description": "Named competitor this positioning displaces. Must be specific.",
        },
        "contradiction": {
            "type": "string",
            "description": "One sentence: what the counter_target cannot concede without walking back prior public claims.",
        },
        "why": {
            "type": "string",
            "description": "The pain — concrete operator + concrete recurring broken workflow. 1-3 sentences.",
        },
        "how": {
            "type": "string",
            "description": "The mechanism — lean into minimalism (SQLite, FTS, hooks, single file). 1-3 sentences.",
        },
        "what": {
            "type": "string",
            "description": "The category claim — 'cloud context' / 'experiential memory' framing. 1-3 sentences.",
        },
    },
}

CODE_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issue_id": {"type": "string", "description": "Short kebab-case identifier"},
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low", "discovered"],
        },
        "target_file": {
            "type": "string",
            "description": "Primary file path the diff modifies",
        },
        "description": {
            "type": "string",
            "description": "One sentence: what the patch does",
        },
        "rationale": {
            "type": "string",
            "description": "1-3 sentences: why this improves the codebase",
        },
        "unified_diff": {
            "type": "string",
            "description": "Complete unified diff for git apply",
        },
        "lines_added": {"type": "integer"},
        "lines_removed": {"type": "integer"},
        "files_touched": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "issue_id",
        "severity",
        "target_file",
        "description",
        "rationale",
        "unified_diff",
        "lines_added",
        "lines_removed",
        "files_touched",
    ],
}


class AgentError(RuntimeError):
    pass


_AGENT_SYSTEM_CACHE: str | None = None


def build_agent_system_prompt() -> str:
    """Cached system prompt: program.md + 3 posts + rubric. Bit-identical across
    calls so the CLI-side prompt cache reuses it across all experiments."""
    global _AGENT_SYSTEM_CACHE
    if _AGENT_SYSTEM_CACHE is not None:
        return _AGENT_SYSTEM_CACHE

    program_path = MOAT_DIR / "program.md"
    program_md = program_path.read_text()

    posts = [
        ("Post 1 — minimalism philosophy", MOAT_GROUND_TRUTH / "post_1_minimalism.md"),
        ("Post 2 — cloud context category", MOAT_GROUND_TRUTH / "post_2_cloud_context.md"),
        ("Post 3 — shared brain for teams", MOAT_GROUND_TRUTH / "post_3_shared_brain.md"),
    ]

    parts: list[str] = [program_md, "\n\n---\n\n## GROUND TRUTH POSTS\n"]
    for label, path in posts:
        parts.append(f"\n### {label}\n\n{path.read_text()}\n")
    parts.append(
        "\n\n---\n\n## RUBRIC (the fitness function — the judge will use this)\n\n"
    )
    parts.append(MOAT_RUBRIC.read_text())
    parts.append(
        "\n\n---\n\n## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate your thinking. Do not write drafts. Each text field "
        "(why, how, what, contradiction) must be 1–3 sentences max — no longer. "
        "Your entire output is the structured object, nothing else. Think "
        "briefly, then emit."
    )

    _AGENT_SYSTEM_CACHE = "".join(parts)
    return _AGENT_SYSTEM_CACHE


def _format_survivors(survivors: list[dict[str, Any]]) -> str:
    if not survivors:
        return "(No recent survivors yet — this is an early experiment. Propose whatever scores highest.)"
    lines = []
    for i, s in enumerate(survivors[-5:], 1):
        lines.append(f"Survivor {i}: [{s.get('mode','?')}] {s.get('persona','?')}")
        lines.append(f"  counter_target: {s.get('counter_target','?')}")
        lines.append(f"  why: {s.get('why','')[:180]}")
        lines.append(f"  how: {s.get('how','')[:180]}")
        lines.append(f"  what: {s.get('what','')[:180]}")
        scores = s.get("scores", {})
        lines.append(
            f"  scored: min={scores.get('minimalism','?')} "
            f"cat={scores.get('category','?')} per={scores.get('persona','?')} "
            f"composite={scores.get('composite','?')}"
        )
        lines.append("")
    return "\n".join(lines)


def propose_candidate(
    mode: str,
    recent_survivors: list[dict[str, Any]] | None = None,
    required_persona: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Generate one candidate proposal. Returns the structured output dict."""
    if mode not in ("refine", "discover"):
        raise ValueError(f"invalid mode: {mode}")

    system_prompt = build_agent_system_prompt()

    survivors_text = _format_survivors(recent_survivors or [])
    persona_hint = (
        f"\n\nFor this experiment, you MUST propose for this persona category: {required_persona}"
        if required_persona
        else ""
    )

    user_prompt = (
        f"Mode: **{mode}**\n\n"
        "Recent survivors (do not repeat these — your proposal must be meaningfully different):\n"
        f"{survivors_text}\n"
        f"{persona_hint}\n\n"
        "Propose one candidate positioning hypothesis. Return structured output matching "
        "the schema. Apply the rubric strictly — a specific operator with a specific "
        "broken ritual beats a clever tagline every time."
    )

    try:
        candidate = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=PROPOSE_CANDIDATE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise AgentError(str(e)) from e

    return candidate


# ---------------------------------------------------------------------------
# Code track
# ---------------------------------------------------------------------------

_CODE_AGENT_SYSTEM_CACHE: str | None = None

ENGINE_SOURCE_FILES = [
    "engine/server.py",
    "engine/db.py",
    "engine/schema.sql",
    "engine/mcp_server.py",
    "engine/sync_conversations.py",
]


def build_code_agent_system_prompt() -> str:
    """Cached system prompt for the code track agent: program.md + 4 posts +
    audit + reference + rubric + engine source. Bit-identical across calls so
    the CLI-side prompt cache reuses it across all experiments."""
    global _CODE_AGENT_SYSTEM_CACHE
    if _CODE_AGENT_SYSTEM_CACHE is not None:
        return _CODE_AGENT_SYSTEM_CACHE

    program_md = (CODE_DIR / "program.md").read_text()

    posts = [
        ("1", "minimalism", CODE_GROUND_TRUTH / "post_1_minimalism.md"),
        ("2", "cloud-context", CODE_GROUND_TRUTH / "post_2_cloud_context.md"),
        ("3", "shared-brain", CODE_GROUND_TRUTH / "post_3_shared_brain.md"),
        ("4", "memory-moat", CODE_GROUND_TRUTH / "post_4_memory_moat.md"),
    ]

    audit_text = (CODE_GROUND_TRUTH / "audit.md").read_text()
    reference_text = (CODE_GROUND_TRUTH / "reference_context_os.md").read_text()
    rubric_text = CODE_RUBRIC.read_text()

    parts: list[str] = []

    # Instructions
    parts.append(f"<instructions>{program_md}</instructions>\n\n")

    # Ground truth posts
    parts.append("<ground-truth>\n")
    for post_id, title, path in posts:
        parts.append(f'<post id="{post_id}" title="{title}">{path.read_text()}</post>\n')
    parts.append("</ground-truth>\n\n")

    # Audit
    parts.append(f"<audit>{audit_text}</audit>\n\n")

    # Reference
    parts.append(f"<reference>{reference_text}</reference>\n\n")

    # Rubric
    parts.append(f"<rubric>{rubric_text}</rubric>\n\n")

    # Engine source files
    parts.append("<engine-source>\n")
    for rel_path in ENGINE_SOURCE_FILES:
        content = (REPO_ROOT / rel_path).read_text()
        parts.append(f'<file path="{rel_path}">{content}</file>\n')
    parts.append("</engine-source>")

    _CODE_AGENT_SYSTEM_CACHE = "".join(parts)
    return _CODE_AGENT_SYSTEM_CACHE


def _format_code_survivors(survivors: list[dict[str, Any]]) -> str:
    if not survivors:
        return "(No recent promoted patches yet — this is an early experiment. Propose whatever scores highest.)"
    lines = []
    for i, s in enumerate(survivors[-5:], 1):
        lines.append(f"Patch {i}: [{s.get('issue_id', '?')}] {s.get('description', '?')}")
        lines.append(f"  target_file: {s.get('target_file', '?')}")
        scores = s.get("scores", {})
        lines.append(
            f"  scored: cor={scores.get('correctness', '?')} "
            f"min={scores.get('minimalism', '?')} rel={scores.get('reliability', '?')} "
            f"tas={scores.get('taste', '?')}"
        )
        lines.append("")
    return "\n".join(lines)


def propose_code_change(
    mode: str,
    recent_survivors: list[dict[str, Any]] | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 240,
) -> dict[str, Any]:
    """Generate one code improvement proposal. Returns the structured output dict."""
    valid_modes = ("critical", "high", "medium", "low", "discover")
    if mode not in valid_modes:
        raise ValueError(f"invalid mode: {mode}, must be one of {valid_modes}")

    system_prompt = build_code_agent_system_prompt()

    survivors_text = _format_code_survivors(recent_survivors or [])

    user_prompt = (
        f"Mode: {mode}\n\n"
        "Recent promoted patches (do not repeat these):\n"
        f"{survivors_text}\n\n"
        "Propose one code improvement. Output a complete unified diff."
    )

    try:
        proposal = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=CODE_CHANGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise AgentError(str(e)) from e

    return proposal
