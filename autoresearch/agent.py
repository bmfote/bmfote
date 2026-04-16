"""
Moat-track candidate proposer. Uses the claude CLI via cli_client.py so
everything routes through Claude Code OAuth. Zero API-key requirement.

The agent assembles a system prompt from program.md + three ground-truth
posts + the rubric. The user prompt contains the mode, recent survivors
(for anti-repetition), and optional persona hint. Output is a structured
candidate dict via --json-schema.
"""

from __future__ import annotations

from typing import Any

from autoresearch.cli_client import CLIError, call_structured
from autoresearch.prepare import MOAT_DIR, MOAT_GROUND_TRUTH, MOAT_RUBRIC

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
