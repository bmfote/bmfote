"""
LLM-as-judge for the moat track — calls the claude CLI via cli_client.py so
everything routes through the user's Claude Code OAuth subscription.
Zero Anthropic-API-key requirement.

The judge assembles a frozen system prompt (three ground-truth posts +
rubric) and a user prompt (the candidate JSON), then asks for a
schema-constrained score via --json-schema on the CLI.

The system prompt text is assembled once per process and is bit-identical
across calls, which lets the CLI's default 1h extended prompt cache reuse
it across all ~80 overnight experiments.
"""

from __future__ import annotations

import json
from typing import Any

from autoresearch.cli_client import CLIError, call_structured
from autoresearch.prepare import MOAT_GROUND_TRUTH, MOAT_RUBRIC

DEFAULT_MODEL = "claude-sonnet-4-5"


class JudgeError(RuntimeError):
    pass


MOAT_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "minimalism",
        "minimalism_reason",
        "category",
        "category_reason",
        "persona",
        "persona_reason",
        "counter_target_valid",
        "counter_target_reason",
        "anti_pattern_words",
    ],
    "properties": {
        "minimalism": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Axis 1: minimalism coherence vs Post 1. Integer 1-10.",
        },
        "minimalism_reason": {
            "type": "string",
            "description": "One concise sentence explaining the minimalism score.",
        },
        "category": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Axis 2: category ownership vs Post 2. Integer 1-10.",
        },
        "category_reason": {"type": "string"},
        "persona": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Axis 3: persona grounding vs Post 3. Integer 1-10.",
        },
        "persona_reason": {"type": "string"},
        "counter_target_valid": {
            "type": "boolean",
            "description": "True iff the contradiction is real, specific, defensible.",
        },
        "counter_target_reason": {"type": "string"},
        "anti_pattern_words": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Marketing words flagged in why/how/what. Empty if none.",
        },
    },
}


_SYSTEM_PROMPT_CACHE: str | None = None


def build_moat_system_prompt() -> str:
    """Assemble the frozen system prompt once. Bit-identical across calls so
    the CLI-side 1h prompt cache reuses it across all overnight experiments."""
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is not None:
        return _SYSTEM_PROMPT_CACHE

    posts = [
        ("Post 1 — minimalism philosophy", MOAT_GROUND_TRUTH / "post_1_minimalism.md"),
        ("Post 2 — cloud context category", MOAT_GROUND_TRUTH / "post_2_cloud_context.md"),
        ("Post 3 — shared brain for teams", MOAT_GROUND_TRUTH / "post_3_shared_brain.md"),
    ]

    parts: list[str] = []
    parts.append(
        "You are the moat-track judge for the bmfote autoresearch harness. "
        "Your job is to score positioning hypotheses against three frozen "
        "ground-truth posts using a three-axis rubric. Be strict, conservative, "
        "and consistent — the same candidate must get the same score every time "
        "you see it. If unsure between two tiers, pick the lower one."
    )
    parts.append("\n\n## GROUND TRUTH POSTS (frozen, never edited)\n")
    for label, path in posts:
        parts.append(f"\n### {label}\n\n{path.read_text()}\n")
    parts.append("\n\n## RUBRIC (frozen, never edited)\n\n")
    parts.append(MOAT_RUBRIC.read_text())
    parts.append(
        "\n\n## YOUR TASK\n\n"
        "The user will send you a candidate positioning hypothesis as a JSON "
        "object with fields: mode, persona, channel, counter_target, "
        "contradiction, why, how, what.\n\n"
        "Score it against the three axes defined in the rubric. Apply the "
        "counter-positioning cap rules. Flag any anti-pattern words found. "
        "Return structured output matching the schema. Integer scores only — "
        "no floats, no ranges, no null. Lower tier if unsure.\n\n"
        "## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate the candidate. Do not explain your tier choices in prose — the "
        "*_reason fields inside the structured output are where reasoning goes, "
        "and each must be ONE concise sentence (max 25 words). Your entire "
        "output is the structured object, nothing else."
    )
    _SYSTEM_PROMPT_CACHE = "".join(parts)
    return _SYSTEM_PROMPT_CACHE


def judge_moat_candidate(
    candidate: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Score one moat candidate. Returns the verdict dict. Raises JudgeError on failure."""
    system_prompt = build_moat_system_prompt()
    user_prompt = (
        "Score this candidate positioning hypothesis. Apply the rubric strictly. "
        "Integer scores only, lower tier if unsure, counter-positioning caps apply.\n\n"
        f"CANDIDATE:\n{json.dumps(candidate, indent=2)}"
    )

    try:
        verdict = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=MOAT_JUDGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise JudgeError(str(e)) from e

    return verdict


def composite_score(verdict: dict[str, Any]) -> float:
    """Weighted composite: 0.35*minimalism + 0.30*category + 0.35*persona."""
    return (
        0.35 * verdict["minimalism"]
        + 0.30 * verdict["category"]
        + 0.35 * verdict["persona"]
    )


def min_axis(verdict: dict[str, Any]) -> int:
    return min(verdict["minimalism"], verdict["category"], verdict["persona"])
