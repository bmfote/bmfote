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
from autoresearch.prepare import (
    CODE_GROUND_TRUTH,
    CODE_RUBRIC,
    MOAT_GROUND_TRUTH,
    MOAT_RUBRIC,
    RECALL_GROUND_TRUTH,
    RECALL_RUBRIC,
    REPO_ROOT,
)

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

CODE_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "correctness": {"type": "integer", "minimum": 1, "maximum": 10},
        "correctness_reason": {"type": "string"},
        "minimalism": {"type": "integer", "minimum": 1, "maximum": 10},
        "minimalism_reason": {"type": "string"},
        "reliability": {"type": "integer", "minimum": 1, "maximum": 10},
        "reliability_reason": {"type": "string"},
        "taste": {"type": "integer", "minimum": 1, "maximum": 10},
        "taste_reason": {"type": "string"},
        "anti_pattern_words": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "correctness",
        "correctness_reason",
        "minimalism",
        "minimalism_reason",
        "reliability",
        "reliability_reason",
        "taste",
        "taste_reason",
        "anti_pattern_words",
    ],
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


# ---------------------------------------------------------------------------
# Code track
# ---------------------------------------------------------------------------

_CODE_JUDGE_SYSTEM_CACHE: str | None = None

ENGINE_SOURCE_FILES = [
    "engine/server.py",
    "engine/db.py",
    "engine/schema.sql",
    "engine/mcp_server.py",
    "engine/sync_conversations.py",
]


def build_code_judge_system_prompt() -> str:
    """Cached system prompt for the code-track judge. Bit-identical across
    calls so the CLI-side prompt cache reuses it across all experiments."""
    global _CODE_JUDGE_SYSTEM_CACHE
    if _CODE_JUDGE_SYSTEM_CACHE is not None:
        return _CODE_JUDGE_SYSTEM_CACHE

    posts = [
        ("1", "minimalism", CODE_GROUND_TRUTH / "post_1_minimalism.md"),
        ("2", "cloud-context", CODE_GROUND_TRUTH / "post_2_cloud_context.md"),
        ("3", "shared-brain", CODE_GROUND_TRUTH / "post_3_shared_brain.md"),
        ("4", "memory-moat", CODE_GROUND_TRUTH / "post_4_memory_moat.md"),
    ]

    audit_text = (CODE_GROUND_TRUTH / "audit.md").read_text()
    rubric_text = CODE_RUBRIC.read_text()

    parts: list[str] = []

    # Role declaration
    parts.append(
        "You are the code-track judge. Score one proposed code improvement "
        "against the four-axis rubric.\n\n"
    )

    # Ground truth posts
    parts.append("<ground-truth>\n")
    for post_id, title, path in posts:
        parts.append(f'<post id="{post_id}" title="{title}">{path.read_text()}</post>\n')
    parts.append("</ground-truth>\n\n")

    # Audit for reference
    parts.append(f"<audit>{audit_text}</audit>\n\n")

    # Rubric
    parts.append(f"<rubric>{rubric_text}</rubric>\n\n")

    # Engine source files
    parts.append("<engine-source>\n")
    for rel_path in ENGINE_SOURCE_FILES:
        content = (REPO_ROOT / rel_path).read_text()
        parts.append(f'<file path="{rel_path}">{content}</file>\n')
    parts.append("</engine-source>\n\n")

    # Task and output rules
    parts.append(
        "## YOUR TASK\n\n"
        "The user will send you a proposed code change as a JSON object with "
        "fields: issue_id, severity, target_file, description, rationale, "
        "unified_diff, lines_added, lines_removed, files_touched. "
        "A validation result will also be provided.\n\n"
        "Score it against the four axes defined in the rubric. Flag any "
        "anti-pattern words found. Return structured output matching the "
        "schema. Integer scores only — no floats, no ranges, no null. Lower "
        "tier if unsure.\n\n"
        "## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate the proposal. Do not explain your tier choices in prose — the "
        "*_reason fields inside the structured output are where reasoning goes, "
        "and each must be ONE concise sentence (max 25 words). Your entire "
        "output is the structured object, nothing else."
    )

    _CODE_JUDGE_SYSTEM_CACHE = "".join(parts)
    return _CODE_JUDGE_SYSTEM_CACHE


def judge_code_change(
    proposal: dict[str, Any],
    validation_result: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Score one code-track proposal. Returns the verdict dict. Raises JudgeError on failure."""
    system_prompt = build_code_judge_system_prompt()

    # Strip internal keys (prefixed with _) from proposal before sending
    clean_proposal = {k: v for k, v in proposal.items() if not k.startswith("_")}

    user_prompt = (
        "Score this proposed code change. Apply the rubric strictly.\n\n"
        f"PROPOSAL:\n{json.dumps(clean_proposal, indent=2)}\n\n"
        f"VALIDATION RESULT:\n{json.dumps(validation_result, indent=2)}"
    )

    try:
        verdict = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=CODE_JUDGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise JudgeError(str(e)) from e

    return verdict


def code_composite_score(verdict: dict[str, Any]) -> float:
    """Weighted composite: 0.30*correctness + 0.25*minimalism + 0.25*reliability + 0.20*taste."""
    return (
        0.30 * verdict["correctness"]
        + 0.25 * verdict["minimalism"]
        + 0.25 * verdict["reliability"]
        + 0.20 * verdict["taste"]
    )


def code_min_axis(verdict: dict[str, Any]) -> int:
    return min(verdict["correctness"], verdict["minimalism"], verdict["reliability"], verdict["taste"])


# ---------------------------------------------------------------------------
# Recall track
# ---------------------------------------------------------------------------

RECALL_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "retrieval": {"type": "integer", "minimum": 1, "maximum": 10},
        "retrieval_reason": {"type": "string"},
        "minimalism": {"type": "integer", "minimum": 1, "maximum": 10},
        "minimalism_reason": {"type": "string"},
        "reliability": {"type": "integer", "minimum": 1, "maximum": 10},
        "reliability_reason": {"type": "string"},
        "taste": {"type": "integer", "minimum": 1, "maximum": 10},
        "taste_reason": {"type": "string"},
        "anti_pattern_words": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "retrieval",
        "retrieval_reason",
        "minimalism",
        "minimalism_reason",
        "reliability",
        "reliability_reason",
        "taste",
        "taste_reason",
        "anti_pattern_words",
    ],
}

_RECALL_JUDGE_SYSTEM_CACHE: str | None = None

RECALL_SOURCE_FILES = ["engine/server.py", "engine/schema.sql"]


def build_recall_judge_system_prompt() -> str:
    """Cached system prompt for the recall-track judge. Bit-identical across
    calls so the CLI-side prompt cache reuses it across all experiments."""
    global _RECALL_JUDGE_SYSTEM_CACHE
    if _RECALL_JUDGE_SYSTEM_CACHE is not None:
        return _RECALL_JUDGE_SYSTEM_CACHE

    posts = [
        ("1", "minimalism", RECALL_GROUND_TRUTH / "post_1_minimalism.md"),
        ("2", "cloud-context", RECALL_GROUND_TRUTH / "post_2_cloud_context.md"),
    ]

    analysis_text = (RECALL_GROUND_TRUTH / "search_analysis.md").read_text()
    rubric_text = RECALL_RUBRIC.read_text()

    parts: list[str] = []

    parts.append(
        "You are the recall-track judge. Score one proposed search improvement "
        "against the four-axis rubric. You receive both the code change AND "
        "quantitative eval metrics (MRR, precision, recall deltas). Use the "
        "measured metrics to ground your retrieval score — don't guess.\n\n"
    )

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
    parts.append("</engine-source>\n\n")

    parts.append(
        "## YOUR TASK\n\n"
        "The user will send you a proposed search change as a JSON object with "
        "fields: change_id, category, target_file, description, rationale, "
        "expected_improvements, unified_diff, lines_added, lines_removed, "
        "files_touched. A validation result and eval metrics will also be "
        "provided.\n\n"
        "The EVAL METRICS include MRR@10 delta, precision@5 delta, recall@5 "
        "delta, number of queries improved/regressed, and per-category MRR "
        "breakdown. Use these numbers directly to score the retrieval axis.\n\n"
        "Score it against the four axes defined in the rubric. Flag any "
        "anti-pattern words found. Return structured output matching the "
        "schema. Integer scores only — no floats, no ranges, no null. Lower "
        "tier if unsure.\n\n"
        "## OUTPUT RULES (CRITICAL FOR LATENCY)\n\n"
        "Do not write any reasoning, preamble, explanation, or summary before "
        "or after the structured output. Do not write a markdown header. Do not "
        "restate the proposal. Do not explain your tier choices in prose — the "
        "*_reason fields inside the structured output are where reasoning goes, "
        "and each must be ONE concise sentence (max 25 words). Your entire "
        "output is the structured object, nothing else."
    )

    _RECALL_JUDGE_SYSTEM_CACHE = "".join(parts)
    return _RECALL_JUDGE_SYSTEM_CACHE


def judge_recall_change(
    proposal: dict[str, Any],
    validation_result: dict[str, Any],
    eval_metrics: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Score one recall-track proposal. Returns the verdict dict. Raises JudgeError on failure."""
    system_prompt = build_recall_judge_system_prompt()

    clean_proposal = {k: v for k, v in proposal.items() if not k.startswith("_")}

    # Strip per-query details from eval metrics to keep the prompt concise
    compact_metrics = {k: v for k, v in eval_metrics.items() if k != "per_query"}

    user_prompt = (
        "Score this proposed search improvement. Apply the rubric strictly.\n\n"
        f"PROPOSAL:\n{json.dumps(clean_proposal, indent=2)}\n\n"
        f"VALIDATION RESULT:\n{json.dumps(validation_result, indent=2)}\n\n"
        f"EVAL METRICS:\n{json.dumps(compact_metrics, indent=2)}"
    )

    try:
        verdict = call_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=RECALL_JUDGE_SCHEMA,
            model=model,
            timeout_s=timeout_s,
        )
    except CLIError as e:
        raise JudgeError(str(e)) from e

    return verdict


def recall_composite_score(verdict: dict[str, Any]) -> float:
    """Weighted composite: 0.40*retrieval + 0.25*minimalism + 0.20*reliability + 0.15*taste."""
    return (
        0.40 * verdict["retrieval"]
        + 0.25 * verdict["minimalism"]
        + 0.20 * verdict["reliability"]
        + 0.15 * verdict["taste"]
    )


def recall_min_axis(verdict: dict[str, Any]) -> int:
    return min(verdict["retrieval"], verdict["minimalism"], verdict["reliability"], verdict["taste"])
