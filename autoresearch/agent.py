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
    CONTEXT_ROT_DIR,
    CONTEXT_ROT_GROUND_TRUTH,
    CONTEXT_ROT_RUBRIC,
    MOAT_DIR,
    MOAT_GROUND_TRUTH,
    MOAT_RUBRIC,
    ONBOARD_DIR,
    ONBOARD_GROUND_TRUTH,
    ONBOARD_RUBRIC,
    RECALL_DIR,
    RECALL_GROUND_TRUTH,
    RECALL_RUBRIC,
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


# ---------------------------------------------------------------------------
# Recall track
# ---------------------------------------------------------------------------

RECALL_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "change_id": {"type": "string", "description": "Short kebab-case identifier"},
        "category": {
            "type": "string",
            "enum": ["query_rewrite", "ranking", "tokenizer", "discover"],
        },
        "target_file": {
            "type": "string",
            "description": "Primary file path the diff modifies",
        },
        "description": {
            "type": "string",
            "description": "One sentence: what the patch does to improve search",
        },
        "rationale": {
            "type": "string",
            "description": "1-3 sentences: why this improves retrieval quality",
        },
        "expected_improvements": {
            "type": "string",
            "description": "Which query categories should improve and why",
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
        "change_id",
        "category",
        "target_file",
        "description",
        "rationale",
        "expected_improvements",
        "unified_diff",
        "lines_added",
        "lines_removed",
        "files_touched",
    ],
}

_RECALL_AGENT_SYSTEM_CACHE: str | None = None

RECALL_SOURCE_FILES = ["engine/server.py", "engine/schema.sql"]


def build_recall_agent_system_prompt() -> str:
    """Cached system prompt for the recall track agent: program.md + 2 posts +
    search_analysis + rubric + engine source. Bit-identical across calls so
    the CLI-side prompt cache reuses it across all experiments."""
    global _RECALL_AGENT_SYSTEM_CACHE
    if _RECALL_AGENT_SYSTEM_CACHE is not None:
        return _RECALL_AGENT_SYSTEM_CACHE

    program_md = (RECALL_DIR / "program.md").read_text()

    posts = [
        ("1", "minimalism", RECALL_GROUND_TRUTH / "post_1_minimalism.md"),
        ("2", "cloud-context", RECALL_GROUND_TRUTH / "post_2_cloud_context.md"),
    ]

    analysis_text = (RECALL_GROUND_TRUTH / "search_analysis.md").read_text()
    rubric_text = RECALL_RUBRIC.read_text()

    parts: list[str] = []
    parts.append(f"<instructions>{program_md}</instructions>\n\n")
    parts.append("<ground-truth>\n")
    for post_id, title, path in posts:
        parts.append(f'<post id="{post_id}" title="{title}">{path.read_text()}</post>\n')
    parts.append("</ground-truth>\n\n")
    parts.append(f"<search-analysis>{analysis_text}</search-analysis>\n\n")
    parts.append(f"<rubric>{rubric_text}</rubric>\n\n")

    parts.append("<engine-source>\n")
    for rel_path in RECALL_SOURCE_FILES:
        content = (REPO_ROOT / rel_path).read_text()
        parts.append(f'<file path="{rel_path}">{content}</file>\n')
    parts.append("</engine-source>")

    parts.append(
        "\n\n---\n\n## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate your thinking. Do not write drafts. Each text field "
        "(description, rationale, expected_improvements) must be 1–3 sentences "
        "max — no longer. Your entire output is the structured object, nothing else."
    )

    _RECALL_AGENT_SYSTEM_CACHE = "".join(parts)
    return _RECALL_AGENT_SYSTEM_CACHE


def _format_recall_survivors(survivors: list[dict[str, Any]]) -> str:
    if not survivors:
        return "(No recent promoted patches yet — this is an early experiment. Propose whatever scores highest.)"
    lines = []
    for i, s in enumerate(survivors[-5:], 1):
        lines.append(f"Patch {i}: [{s.get('change_id', '?')}] {s.get('description', '?')}")
        lines.append(f"  target_file: {s.get('target_file', '?')}")
        lines.append(f"  category: {s.get('category', '?')}")
        scores = s.get("scores", {})
        lines.append(
            f"  scored: ret={scores.get('retrieval', '?')} "
            f"min={scores.get('minimalism', '?')} rel={scores.get('reliability', '?')} "
            f"tas={scores.get('taste', '?')}"
        )
        metrics = s.get("eval_metrics", {})
        if metrics and not metrics.get("eval_unavailable"):
            lines.append(
                f"  eval: mrr_delta={metrics.get('mrr_delta', '?')} "
                f"p5_delta={metrics.get('p5_delta', '?')}"
            )
        lines.append("")
    return "\n".join(lines)


def propose_recall_change(
    mode: str,
    recent_survivors: list[dict[str, Any]] | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 240,
) -> dict[str, Any]:
    """Generate one recall improvement proposal. Returns the structured output dict."""
    valid_modes = ("query_rewrite", "ranking", "tokenizer", "discover")
    if mode not in valid_modes:
        raise ValueError(f"invalid mode: {mode}, must be one of {valid_modes}")

    system_prompt = build_recall_agent_system_prompt()

    survivors_text = _format_recall_survivors(recent_survivors or [])

    user_prompt = (
        f"Mode: {mode}\n\n"
        "Recent promoted patches (do not repeat these):\n"
        f"{survivors_text}\n\n"
        "Propose one search improvement. Output a complete unified diff."
    )

    try:
        proposal = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=RECALL_CHANGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise AgentError(str(e)) from e

    return proposal


# ---------------------------------------------------------------------------
# Context-rot track
# ---------------------------------------------------------------------------

CONTEXT_ROT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "mode",
        "definition",
        "manifestation",
        "cost_model",
        "inevitability",
        "counter_narrative",
        "evidence_anchor",
    ],
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["define", "quantify", "narrate", "counter"],
            "description": "Must match the mode from the user prompt.",
        },
        "definition": {
            "type": "string",
            "description": "One-sentence definition of context rot. Crisp, non-technical.",
        },
        "manifestation": {
            "type": "string",
            "description": "How context rot shows up: specific scenario a buyer would recognize.",
        },
        "cost_model": {
            "type": "string",
            "description": "Economic impact quantified: time/money/accuracy loss with numbers.",
        },
        "inevitability": {
            "type": "string",
            "description": "Why this gets worse as AI adoption grows. Structural, not fixable.",
        },
        "counter_narrative": {
            "type": "string",
            "description": "Name an 'obvious fix' and why it fails. Must cite evidence.",
        },
        "evidence_anchor": {
            "type": "string",
            "description": "Which practitioner quote or paper supports this framing (by name).",
        },
    },
}

_CONTEXT_ROT_AGENT_SYSTEM_CACHE: str | None = None


def build_context_rot_agent_system_prompt() -> str:
    """Cached system prompt for the context-rot track agent."""
    global _CONTEXT_ROT_AGENT_SYSTEM_CACHE
    if _CONTEXT_ROT_AGENT_SYSTEM_CACHE is not None:
        return _CONTEXT_ROT_AGENT_SYSTEM_CACHE

    program_md = (CONTEXT_ROT_DIR / "program.md").read_text()

    posts = [
        ("Post 1 — minimalism philosophy", CONTEXT_ROT_GROUND_TRUTH / "post_1_minimalism.md"),
        ("Post 2 — cloud context category", CONTEXT_ROT_GROUND_TRUTH / "post_2_cloud_context.md"),
        ("Post 3 — shared brain for teams", CONTEXT_ROT_GROUND_TRUTH / "post_3_shared_brain.md"),
    ]

    evidence_text = (CONTEXT_ROT_GROUND_TRUTH / "evidence.md").read_text()
    problem_text = (CONTEXT_ROT_GROUND_TRUTH / "problem_definition.md").read_text()
    rubric_text = CONTEXT_ROT_RUBRIC.read_text()

    parts: list[str] = [program_md, "\n\n---\n\n## GROUND TRUTH POSTS\n"]
    for label, path in posts:
        parts.append(f"\n### {label}\n\n{path.read_text()}\n")

    parts.append(f"\n\n---\n\n<evidence>{evidence_text}</evidence>\n\n")
    parts.append(f"<problem-definition>{problem_text}</problem-definition>\n\n")
    parts.append(
        "\n\n---\n\n## RUBRIC (the fitness function — the judge will use this)\n\n"
    )
    parts.append(rubric_text)
    parts.append(
        "\n\n---\n\n## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate your thinking. Each text field must be 1–3 sentences max. "
        "Your entire output is the structured object, nothing else."
    )

    _CONTEXT_ROT_AGENT_SYSTEM_CACHE = "".join(parts)
    return _CONTEXT_ROT_AGENT_SYSTEM_CACHE


def _format_context_rot_survivors(survivors: list[dict[str, Any]]) -> str:
    if not survivors:
        return "(No recent survivors yet — this is an early experiment. Propose whatever scores highest.)"
    lines = []
    for i, s in enumerate(survivors[-5:], 1):
        lines.append(f"Survivor {i}: [{s.get('mode', '?')}]")
        lines.append(f"  definition: {s.get('definition', '')[:180]}")
        lines.append(f"  manifestation: {s.get('manifestation', '')[:180]}")
        lines.append(f"  cost_model: {s.get('cost_model', '')[:180]}")
        lines.append(f"  counter_narrative: {s.get('counter_narrative', '')[:180]}")
        scores = s.get("scores", {})
        lines.append(
            f"  scored: leg={scores.get('legibility', '?')} "
            f"eco={scores.get('economic', '?')} inv={scores.get('inevitability', '?')} "
            f"composite={scores.get('composite', '?')}"
        )
        lines.append("")
    return "\n".join(lines)


def propose_context_rot(
    mode: str,
    recent_survivors: list[dict[str, Any]] | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Generate one context-rot problem definition."""
    if mode not in ("define", "quantify", "narrate", "counter"):
        raise ValueError(f"invalid mode: {mode}")

    system_prompt = build_context_rot_agent_system_prompt()
    survivors_text = _format_context_rot_survivors(recent_survivors or [])

    user_prompt = (
        f"Mode: **{mode}**\n\n"
        "Recent survivors (do not repeat these — your proposal must be meaningfully different):\n"
        f"{survivors_text}\n\n"
        "Propose one context-rot problem definition. Return structured output matching "
        "the schema. Each field 1–3 sentences max. Cite evidence."
    )

    try:
        candidate = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=CONTEXT_ROT_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise AgentError(str(e)) from e

    return candidate


# ---------------------------------------------------------------------------
# Onboard track — silent-failure guards
# ---------------------------------------------------------------------------

ONBOARD_MODES = (
    "mcp_verify",
    "mcp_reachable",
    "token_shape",
    "restart_nudge",
    "hooks_fired",
    "discover",
)

ONBOARD_ALLOWED_TARGETS = (
    "installer/setup.sh",
    "bin/cli.js",
    "hooks/post-compaction-context.sh",
    "hooks/stop.sh",
    "hooks/sync-transcript.sh",
)

ONBOARD_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "change_id": {"type": "string", "description": "Short kebab-case identifier"},
        "mode": {
            "type": "string",
            "enum": list(ONBOARD_MODES),
            "description": "Must match the mode in the user prompt",
        },
        "target_file": {
            "type": "string",
            "enum": list(ONBOARD_ALLOWED_TARGETS),
            "description": "One of the allowed install-surface files",
        },
        "anchor_line": {
            "type": "string",
            "description": "An existing line in target_file that appears EXACTLY ONCE. Insertion goes immediately after this line. Must match character-for-character including leading whitespace.",
        },
        "insertion_lines": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string"},
            "description": "1-8 lines to insert after anchor_line. No leading '+', no trailing newlines. Preserve indentation.",
        },
        "failure_modes_addressed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Failure-mode IDs from failure_modes.md (e.g. ['F4']) or ['new']",
        },
        "description": {
            "type": "string",
            "description": "One precise sentence naming what the guard detects",
        },
        "rationale": {
            "type": "string",
            "description": "1-3 sentences: why this failure mode exists and why the guard closes it",
        },
        "error_message": {
            "type": "string",
            "description": "The single ERROR line the guard echoes (two-space indent + 'ERROR: ...'). Must name what broke.",
        },
        "next_command": {
            "type": "string",
            "description": "The exact shell command the user can run to diagnose or fix. Must be universally applicable (no sudo-only, no macOS-only).",
        },
        "expected_impact": {
            "type": "string",
            "description": "Which users benefit and how the time-to-value metric moves",
        },
    },
    "required": [
        "change_id",
        "mode",
        "target_file",
        "anchor_line",
        "insertion_lines",
        "failure_modes_addressed",
        "description",
        "rationale",
        "error_message",
        "next_command",
        "expected_impact",
    ],
}

_ONBOARD_AGENT_SYSTEM_CACHE: str | None = None

# Files the agent sees. These are the install surface — mirrors the scope
# guardrails in program.md / rubric.md.
ONBOARD_SOURCE_FILES = [
    "installer/setup.sh",
    "bin/cli.js",
    "hooks/post-compaction-context.sh",
    "hooks/pre-compaction-context.sh",
    "hooks/stop.sh",
    "hooks/sync-transcript.sh",
]


def build_onboard_agent_system_prompt() -> str:
    """Cached system prompt for the onboard track agent: program.md + 4 ground-truth
    docs (including winning_pattern.md) + rubric + install-surface source. Bit-identical
    across calls so the CLI-side prompt cache reuses it across all experiments."""
    global _ONBOARD_AGENT_SYSTEM_CACHE
    if _ONBOARD_AGENT_SYSTEM_CACHE is not None:
        return _ONBOARD_AGENT_SYSTEM_CACHE

    program_md = (ONBOARD_DIR / "program.md").read_text()
    install_surface = (ONBOARD_GROUND_TRUTH / "install_surface.md").read_text()
    failure_modes = (ONBOARD_GROUND_TRUTH / "failure_modes.md").read_text()
    target_metric = (ONBOARD_GROUND_TRUTH / "target_metric.md").read_text()
    winning_pattern = (ONBOARD_GROUND_TRUTH / "winning_pattern.md").read_text()
    rubric_text = ONBOARD_RUBRIC.read_text()

    parts: list[str] = []
    parts.append(f"<instructions>{program_md}</instructions>\n\n")
    parts.append(f"<winning-pattern>{winning_pattern}</winning-pattern>\n\n")
    parts.append(f"<install-surface>{install_surface}</install-surface>\n\n")
    parts.append(f"<failure-modes>{failure_modes}</failure-modes>\n\n")
    parts.append(f"<target-metric>{target_metric}</target-metric>\n\n")
    parts.append(f"<rubric>{rubric_text}</rubric>\n\n")

    parts.append("<install-source>\n")
    for rel_path in ONBOARD_SOURCE_FILES:
        full = REPO_ROOT / rel_path
        if not full.exists():
            continue
        content = full.read_text()
        parts.append(f'<file path="{rel_path}">{content}</file>\n')
    parts.append("</install-source>")

    parts.append(
        "\n\n---\n\n## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Each "
        "text field must be 1-3 sentences max. insertion_lines must be 1-8 "
        "lines; the anchor_line must appear EXACTLY ONCE in target_file. "
        "Your entire output is the structured object, nothing else."
    )

    _ONBOARD_AGENT_SYSTEM_CACHE = "".join(parts)
    return _ONBOARD_AGENT_SYSTEM_CACHE


def _format_onboard_survivors(survivors: list[dict[str, Any]]) -> str:
    if not survivors:
        return "(No recent promoted patches yet — propose whatever scores highest. Match the canonical pattern in <winning-pattern>.)"
    lines = []
    for i, s in enumerate(survivors[-5:], 1):
        lines.append(f"Patch {i}: [{s.get('change_id', '?')}] {s.get('description', '?')}")
        lines.append(f"  target_file: {s.get('target_file', '?')}  mode: {s.get('mode', '?')}")
        lines.append(f"  failure_modes: {','.join(s.get('failure_modes_addressed', []))}")
        lines.append(f"  anchor: {(s.get('anchor_line', '') or '')[:80]!r}")
        scores = s.get("scores", {})
        lines.append(
            f"  scored: gpf={scores.get('guard_pattern_fidelity', '?')} "
            f"ttv={scores.get('time_to_value', '?')} "
            f"fmc={scores.get('failure_mode_coverage', '?')} "
            f"ec={scores.get('error_craftsmanship', '?')} "
            f"composite={scores.get('composite', '?')}"
        )
        lines.append("")
    return "\n".join(lines)


def propose_onboard_change(
    mode: str,
    recent_survivors: list[dict[str, Any]] | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 240,
) -> dict[str, Any]:
    """Generate one silent-failure guard proposal. Returns the structured output dict."""
    if mode not in ONBOARD_MODES:
        raise ValueError(f"invalid mode: {mode}, must be one of {ONBOARD_MODES}")

    system_prompt = build_onboard_agent_system_prompt()
    survivors_text = _format_onboard_survivors(recent_survivors or [])

    user_prompt = (
        f"Mode: **{mode}**\n\n"
        "Recent promoted patches (do not repeat the same anchor region):\n"
        f"{survivors_text}\n\n"
        "Propose one silent-failure guard matching the canonical pattern in "
        "<winning-pattern>. Emit anchor_line + insertion_lines (1-8 lines). "
        "Anchor MUST appear exactly once in target_file."
    )

    try:
        proposal = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=ONBOARD_CHANGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise AgentError(str(e)) from e

    return proposal
