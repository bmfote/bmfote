"""
Autoresearch runner — moat + code tracks.

Two tracks share the same loop skeleton:
- **moat**: agent emits positioning hypotheses as structured JSON (no file editing)
- **code**: agent emits unified diffs, runner applies them in git worktrees,
  validates (syntax + imports), and the judge scores the result

Usage:
    python -m autoresearch.runner --dry-run
    python -m autoresearch.runner --track moat --max-experiments 80
    python -m autoresearch.runner --track code --max-experiments 80
    python -m autoresearch.runner --release-lock

The loop:
    1. prepare.verify_safety()              (branch + env + lock + ground-truth hashes)
    2. for i in range(max_experiments):
         mode = rotate_mode(i)              (refine, discover, refine, ...)
         persona = rotate_persona(i, mode)  (discover only — cycles 4 personas)
         survivors = load_last_n_survivors(5)
         candidate = agent.propose_candidate(mode, survivors, persona)
         verdict   = judge.judge_moat_candidate(candidate)
         promoted  = gate_check(verdict, current_best)
         log_experiment(...)                (always — success or fail)
         if promoted: append_to_target_jsonl + update_best
         if i % 10 == 9: drift_alarm()
    3. prepare.release_lock()
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autoresearch import agent, judge, prepare, eval_common
from autoresearch.eval_common import (
    Experiment,
    append_jsonl,
    apply_diff,
    construct_insertion_diff,
    git_worktree_add,
    git_worktree_remove,
    load_best,
    log_experiment,
    now,
    save_best,
    save_patch,
    scope_check,
    validate_onboard_worktree,
    validate_worktree,
)
from autoresearch.judge import (
    code_composite_score,
    code_min_axis,
    composite_score,
    context_rot_composite_score,
    context_rot_min_axis,
    distribution_composite_score,
    distribution_min_axis,
    min_axis,
    onboard_composite_score,
    onboard_min_axis,
    recall_composite_score,
    recall_min_axis,
)

MOAT_TARGET_JSONL = prepare.MOAT_DIR / "target.jsonl"
CODE_TARGET_JSONL = prepare.CODE_DIR / "target.jsonl"
RECALL_TARGET_JSONL = prepare.RECALL_DIR / "target.jsonl"
CONTEXT_ROT_TARGET_JSONL = prepare.CONTEXT_ROT_DIR / "target.jsonl"
ONBOARD_TARGET_JSONL = prepare.ONBOARD_DIR / "target.jsonl"
DISTRIBUTION_TARGET_JSONL = prepare.DISTRIBUTION_DIR / "target.jsonl"

# Promotion gate — designed for a "ranked list of good pitches" artifact,
# not strict monotonic improvement. Once the agent hits the top of the scale,
# a delta gate would discard every subsequent ~9.5+ variation and the morning
# JSONL would have 1 entry. Instead: promote any pitch that clears an absolute
# floor. best/moat.json still tracks the true top for drift-alarm purposes.
PROMOTION_COMPOSITE_FLOOR = 8.0
PROMOTION_MIN_AXIS = 6

# Drift alarm
DRIFT_CHECK_EVERY = 10
DRIFT_HALT_THRESHOLD = 0.7

# Graceful shutdown flag (set by SIGINT/SIGTERM handler)
_SHUTDOWN_REQUESTED = False


def _install_signal_handlers() -> None:
    def handler(signum, frame):
        global _SHUTDOWN_REQUESTED
        if _SHUTDOWN_REQUESTED:
            print("\n[runner] second signal — exiting immediately", flush=True)
            sys.exit(130)
        _SHUTDOWN_REQUESTED = True
        print(
            f"\n[runner] signal {signum} received — finishing current experiment and stopping",
            flush=True,
        )

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _rotate_mode(i: int) -> str:
    """Alternate refine/discover. Even = refine (anchors on Post 3), odd = discover."""
    return "refine" if i % 2 == 0 else "discover"


def _rotate_persona(i: int) -> str | None:
    """Cycle through the 4 personas for discover-mode experiments only.
    Refine mode gets None (agent stays on SMB operators from Post 3)."""
    if i % 2 == 0:  # refine
        return None
    personas = [
        "dev-first small teams",
        "agencies & consultancies",
        "fractional / solo operators",
        "SMB operators",
    ]
    return personas[(i // 2) % 4]


def _load_last_n_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not MOAT_TARGET_JSONL.exists():
        return []
    lines = MOAT_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2 :]:  # read a few extra in case some are malformed
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _strip_meta(d: dict[str, Any]) -> dict[str, Any]:
    """Remove _usage / _elapsed_s before passing candidate to the judge."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    """Absolute-floor gate: promote any pitch clearing the bar. Monotonic-improvement
    is not required — we want a ranked list of good pitches for morning review."""
    composite = composite_score(verdict)
    min_ax = min_axis(verdict)

    if not verdict.get("counter_target_valid", False):
        return (False, "counter_target_valid is false")

    if min_ax < PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {PROMOTION_MIN_AXIS}")

    if composite < PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {PROMOTION_COMPOSITE_FLOOR}")


def _promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
) -> dict[str, Any]:
    """Write survivor to target.jsonl and update best/moat.json."""
    composite = composite_score(verdict)
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "mode": candidate["mode"],
        "persona": candidate["persona"],
        "channel": candidate.get("channel", ""),
        "counter_target": candidate["counter_target"],
        "contradiction": candidate["contradiction"],
        "why": candidate["why"],
        "how": candidate["how"],
        "what": candidate["what"],
        "scores": {
            "minimalism": verdict["minimalism"],
            "category": verdict["category"],
            "persona": verdict["persona"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "minimalism": verdict.get("minimalism_reason", ""),
            "category": verdict.get("category_reason", ""),
            "persona": verdict.get("persona_reason", ""),
        },
        "counter_target_valid": verdict.get("counter_target_valid", False),
        "counter_target_reason": verdict.get("counter_target_reason", ""),
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
    }
    append_jsonl(MOAT_TARGET_JSONL, survivor)
    # best/moat.json tracks the true top for drift-alarm purposes — only
    # overwrite it when the new composite is strictly greater than the prior best.
    prior = load_best("moat")
    if composite > prior.get("composite", 0.0):
        save_best(
            "moat",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
            },
        )
    return survivor


def _drift_check(experiment_i: int) -> dict[str, Any] | None:
    """Every 10 experiments, re-score the current best. If it moves by more
    than DRIFT_HALT_THRESHOLD, log an alarm. Returns drift info dict or None."""
    best = load_best("moat")
    if not best.get("candidate"):
        return None
    candidate = best["candidate"]
    try:
        verdict = judge.judge_moat_candidate(candidate)
    except judge.JudgeError as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}
    new_composite = composite_score(verdict)
    old_composite = best.get("composite", 0.0)
    delta = abs(new_composite - old_composite)
    alarm = delta > DRIFT_HALT_THRESHOLD
    info = {
        "experiment": experiment_i,
        "old_composite": round(old_composite, 3),
        "new_composite": round(new_composite, 3),
        "delta": round(delta, 3),
        "alarm": alarm,
    }
    append_jsonl(prepare.STATE_DIR / "drift.jsonl", info)
    return info


def run_moat_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the moat loop. Returns number of experiments actually executed."""
    print(f"[runner] moat track — max {max_experiments} experiments", flush=True)
    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_mode(i)
        persona_hint = _rotate_persona(i)
        survivors = _load_last_n_survivors(5)
        current_best = load_best("moat")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="moat",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        candidate = None
        verdict = None
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:8s} persona={persona_hint or '(refine-smb)':<30s} best={current_best_composite:.2f}",
                flush=True,
            )
            candidate = agent.propose_candidate(
                mode=mode,
                recent_survivors=survivors,
                required_persona=persona_hint,
            )
            verdict = judge.judge_moat_candidate(_strip_meta(candidate))
            ok, reason = _gate_check(verdict)

            exp.candidate = _strip_meta(candidate)
            exp.score = round(composite_score(verdict), 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.eval_breakdown = {
                "minimalism": verdict["minimalism"],
                "category": verdict["category"],
                "persona": verdict["persona"],
                "counter_target_valid": verdict.get("counter_target_valid", False),
                "anti_pattern_words": verdict.get("anti_pattern_words", []),
                "reasons": {
                    "minimalism": verdict.get("minimalism_reason", ""),
                    "category": verdict.get("category_reason", ""),
                    "persona": verdict.get("persona_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
            }
            exp.agent_exit = 0

            if ok:
                _promote(candidate, verdict, i)
                promoted += 1

            elapsed = time.time() - exp_started
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={exp.score:.2f} "
                f"({verdict['minimalism']}/{verdict['category']}/{verdict['persona']}) "
                f"counter={'OK' if verdict.get('counter_target_valid') else 'FAIL'} "
                f"t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            log_experiment(exp)

        # Drift check every N experiments (after promotion, using current best)
        if (i + 1) % DRIFT_CHECK_EVERY == 0:
            info = _drift_check(i)
            if info and info.get("alarm"):
                print(
                    f"[runner] DRIFT ALARM — current best re-scored from "
                    f"{info['old_composite']} to {info['new_composite']} "
                    f"(delta {info['delta']}). Halting.",
                    flush=True,
                )
                break
            elif info and "drift_check_failed" in info:
                print(f"[runner] drift check failed: {info['drift_check_failed']}", flush=True)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


# ---------------------------------------------------------------------------
# Code track
# ---------------------------------------------------------------------------

CODE_PROMOTION_COMPOSITE_FLOOR = 7.5
CODE_PROMOTION_MIN_AXIS = 5
CODE_ALLOWED_PATHS = ["engine/"]

_CODE_MODE_SEQUENCE = [
    "critical", "high", "critical", "high", "medium",
    "high", "discover", "high", "critical", "high",
]


def _rotate_code_mode(i: int) -> str:
    return _CODE_MODE_SEQUENCE[i % len(_CODE_MODE_SEQUENCE)]


def _load_code_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not CODE_TARGET_JSONL.exists():
        return []
    lines = CODE_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2 :]:
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _code_gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    composite = code_composite_score(verdict)
    min_ax = code_min_axis(verdict)

    if min_ax < CODE_PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {CODE_PROMOTION_MIN_AXIS}")

    if composite < CODE_PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {CODE_PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {CODE_PROMOTION_COMPOSITE_FLOOR}")


def _code_promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    composite = code_composite_score(verdict)
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "issue_id": candidate.get("issue_id", "unknown"),
        "severity": candidate.get("severity", "unknown"),
        "target_file": candidate.get("target_file", ""),
        "description": candidate.get("description", ""),
        "rationale": candidate.get("rationale", ""),
        "files_touched": candidate.get("files_touched", []),
        "lines_added": candidate.get("lines_added", 0),
        "lines_removed": candidate.get("lines_removed", 0),
        "unified_diff": candidate.get("unified_diff", ""),
        "scores": {
            "correctness": verdict["correctness"],
            "minimalism": verdict["minimalism"],
            "reliability": verdict["reliability"],
            "taste": verdict["taste"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "correctness": verdict.get("correctness_reason", ""),
            "minimalism": verdict.get("minimalism_reason", ""),
            "reliability": verdict.get("reliability_reason", ""),
            "taste": verdict.get("taste_reason", ""),
        },
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
        "validation": validation_result,
    }
    append_jsonl(CODE_TARGET_JSONL, survivor)

    # Save standalone .patch file
    save_patch(
        experiment_i=experiment_i,
        issue_id=candidate.get("issue_id", "unknown"),
        diff_text=candidate.get("unified_diff", ""),
    )

    prior = load_best("code")
    if composite > prior.get("composite", 0.0):
        save_best(
            "code",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
            },
        )
    return survivor


def _code_drift_check(experiment_i: int) -> dict[str, Any] | None:
    best = load_best("code")
    if not best.get("candidate"):
        return None
    candidate = best["candidate"]
    validation = {"syntax_ok": True, "import_ok": True, "syntax_errors": [], "import_error": None}
    try:
        verdict = judge.judge_code_change(candidate, validation)
    except judge.JudgeError as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}
    new_composite = code_composite_score(verdict)
    old_composite = best.get("composite", 0.0)
    delta = abs(new_composite - old_composite)
    alarm = delta > DRIFT_HALT_THRESHOLD
    info = {
        "experiment": experiment_i,
        "old_composite": round(old_composite, 3),
        "new_composite": round(new_composite, 3),
        "delta": round(delta, 3),
        "alarm": alarm,
    }
    append_jsonl(prepare.STATE_DIR / "drift.jsonl", info)
    return info


def run_code_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the code improvement loop. Returns number of experiments executed."""
    print(f"[runner] code track — max {max_experiments} experiments", flush=True)
    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_code_mode(i)
        survivors = _load_code_survivors(5)
        current_best = load_best("code")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="code",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        wt_dir = None
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:10s} best={current_best_composite:.2f}",
                flush=True,
            )

            # 1. Agent proposes a code change
            candidate = agent.propose_code_change(
                mode=mode,
                recent_survivors=survivors,
            )

            exp.candidate = _strip_meta(candidate)
            exp.agent_exit = 0

            # 2. Create worktree and apply diff
            wt_dir = git_worktree_add("AR")
            diff_text = candidate.get("unified_diff", "")

            if not diff_text.strip():
                exp.error = "empty unified_diff"
                exp.gate_reason = "validation: empty diff"
                failures += 1
                print(f"[runner]   SKIP: empty diff", flush=True)
                continue

            apply_ok, apply_err = apply_diff(wt_dir, diff_text)
            if not apply_ok:
                exp.error = f"git apply failed: {apply_err[:200]}"
                exp.gate_reason = "validation: diff does not apply"
                failures += 1
                print(f"[runner]   SKIP: diff does not apply — {apply_err[:100]}", flush=True)
                continue

            # 3. Scope check
            scope_ok, violated = scope_check(wt_dir, CODE_ALLOWED_PATHS)
            if not scope_ok:
                exp.violated_scope = True
                exp.violated_paths = violated
                exp.gate_reason = f"validation: scope violation {violated}"
                failures += 1
                print(f"[runner]   SKIP: scope violation — {violated}", flush=True)
                continue

            # 4. Validate (syntax + imports)
            validation_result = validate_worktree(wt_dir)
            if not validation_result["syntax_ok"]:
                exp.error = f"syntax errors: {validation_result['syntax_errors']}"
                exp.gate_reason = "validation: syntax error"
                failures += 1
                print(f"[runner]   SKIP: syntax error — {validation_result['syntax_errors'][:100]}", flush=True)
                continue

            if not validation_result["import_ok"]:
                exp.error = f"import error: {validation_result['import_error']}"
                exp.gate_reason = "validation: import error"
                failures += 1
                print(f"[runner]   SKIP: import error — {validation_result['import_error'][:100]}", flush=True)
                continue

            # 5. Judge scores the change
            verdict = judge.judge_code_change(_strip_meta(candidate), validation_result)
            ok, reason = _code_gate_check(verdict)

            composite = code_composite_score(verdict)
            exp.score = round(composite, 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.eval_breakdown = {
                "correctness": verdict["correctness"],
                "minimalism": verdict["minimalism"],
                "reliability": verdict["reliability"],
                "taste": verdict["taste"],
                "anti_pattern_words": verdict.get("anti_pattern_words", []),
                "reasons": {
                    "correctness": verdict.get("correctness_reason", ""),
                    "minimalism": verdict.get("minimalism_reason", ""),
                    "reliability": verdict.get("reliability_reason", ""),
                    "taste": verdict.get("taste_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
                "validation": validation_result,
            }

            if ok:
                _code_promote(candidate, verdict, i, validation_result)
                promoted += 1

            elapsed = time.time() - exp_started
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={composite:.2f} "
                f"({verdict['correctness']}/{verdict['minimalism']}/{verdict['reliability']}/{verdict['taste']}) "
                f"issue={candidate.get('issue_id', '?')} "
                f"t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            if wt_dir is not None:
                git_worktree_remove(wt_dir)
            log_experiment(exp)

        # Drift check every N experiments
        if (i + 1) % DRIFT_CHECK_EVERY == 0:
            info = _code_drift_check(i)
            if info and info.get("alarm"):
                print(
                    f"[runner] DRIFT ALARM — current best re-scored from "
                    f"{info['old_composite']} to {info['new_composite']} "
                    f"(delta {info['delta']}). Halting.",
                    flush=True,
                )
                break
            elif info and "drift_check_failed" in info:
                print(f"[runner] drift check failed: {info['drift_check_failed']}", flush=True)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


# ---------------------------------------------------------------------------
# Recall track
# ---------------------------------------------------------------------------

RECALL_PROMOTION_COMPOSITE_FLOOR = 7.5
RECALL_PROMOTION_MIN_AXIS = 5
RECALL_ALLOWED_PATHS = ["engine/"]

# Recall track has objective eval metrics (MRR, precision, recall) so judge
# score drift matters less than on moat/code tracks. Widen the threshold to
# avoid halting runs over normal LLM scoring variance.
RECALL_DRIFT_HALT_THRESHOLD = 2.0

_RECALL_MODE_SEQUENCE = [
    "query_rewrite", "ranking", "query_rewrite", "tokenizer",
    "query_rewrite", "discover", "ranking", "query_rewrite",
]


def _rotate_recall_mode(i: int) -> str:
    return _RECALL_MODE_SEQUENCE[i % len(_RECALL_MODE_SEQUENCE)]


def _load_recall_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not RECALL_TARGET_JSONL.exists():
        return []
    lines = RECALL_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _recall_gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    composite = recall_composite_score(verdict)
    min_ax = recall_min_axis(verdict)

    if min_ax < RECALL_PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {RECALL_PROMOTION_MIN_AXIS}")

    if composite < RECALL_PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {RECALL_PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {RECALL_PROMOTION_COMPOSITE_FLOOR}")


def _recall_promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
    validation_result: dict[str, Any],
    eval_metrics: dict[str, Any],
) -> dict[str, Any]:
    composite = recall_composite_score(verdict)
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "change_id": candidate.get("change_id", "unknown"),
        "category": candidate.get("category", "unknown"),
        "target_file": candidate.get("target_file", ""),
        "description": candidate.get("description", ""),
        "rationale": candidate.get("rationale", ""),
        "expected_improvements": candidate.get("expected_improvements", ""),
        "files_touched": candidate.get("files_touched", []),
        "lines_added": candidate.get("lines_added", 0),
        "lines_removed": candidate.get("lines_removed", 0),
        "unified_diff": candidate.get("unified_diff", ""),
        "scores": {
            "retrieval": verdict["retrieval"],
            "minimalism": verdict["minimalism"],
            "reliability": verdict["reliability"],
            "taste": verdict["taste"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "retrieval": verdict.get("retrieval_reason", ""),
            "minimalism": verdict.get("minimalism_reason", ""),
            "reliability": verdict.get("reliability_reason", ""),
            "taste": verdict.get("taste_reason", ""),
        },
        "eval_metrics": {k: v for k, v in eval_metrics.items() if k != "per_query"},
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
        "validation": validation_result,
    }
    append_jsonl(RECALL_TARGET_JSONL, survivor)

    save_patch(
        experiment_i=experiment_i,
        issue_id=candidate.get("change_id", "unknown"),
        diff_text=candidate.get("unified_diff", ""),
        track_dir=prepare.RECALL_DIR,
    )

    prior = load_best("recall")
    if composite > prior.get("composite", 0.0):
        save_best(
            "recall",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
                "eval_metrics": {k: v for k, v in eval_metrics.items() if k != "per_query"},
            },
        )
    return survivor


def _recall_drift_check(experiment_i: int) -> dict[str, Any] | None:
    best = load_best("recall")
    if not best.get("candidate"):
        return None
    candidate = best["candidate"]
    validation = {"syntax_ok": True, "import_ok": True, "syntax_errors": [], "import_error": None}
    eval_metrics = best.get("eval_metrics", {})
    try:
        verdict = judge.judge_recall_change(candidate, validation, eval_metrics)
    except judge.JudgeError as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}
    new_composite = recall_composite_score(verdict)
    old_composite = best.get("composite", 0.0)
    delta = abs(new_composite - old_composite)
    alarm = delta > RECALL_DRIFT_HALT_THRESHOLD
    info = {
        "experiment": experiment_i,
        "old_composite": round(old_composite, 3),
        "new_composite": round(new_composite, 3),
        "delta": round(delta, 3),
        "alarm": alarm,
    }
    append_jsonl(prepare.STATE_DIR / "drift.jsonl", info)
    return info


def run_recall_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the recall improvement loop. Returns number of experiments executed."""
    from autoresearch.eval_recall import compute_baseline, run_eval

    print(f"[runner] recall track — max {max_experiments} experiments", flush=True)

    # Compute baseline metrics once
    db_path = prepare.REPO_ROOT / "engine" / "local-replica.db"
    eval_queries_path = prepare.RECALL_GROUND_TRUTH / "eval_queries.jsonl"
    print("[runner] computing baseline search metrics...", flush=True)
    baseline = compute_baseline(db_path, eval_queries_path)
    print(
        f"[runner] baseline: MRR@10={baseline['mrr_10']:.4f} "
        f"P@5={baseline['mean_precision_5']:.4f} "
        f"R@5={baseline['mean_recall_5']:.4f} "
        f"hits={baseline['hits']}/{baseline['total']}",
        flush=True,
    )

    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_recall_mode(i)
        survivors = _load_recall_survivors(5)
        current_best = load_best("recall")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="recall",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        wt_dir = None
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:14s} best={current_best_composite:.2f}",
                flush=True,
            )

            # 1. Agent proposes a search change
            candidate = agent.propose_recall_change(
                mode=mode,
                recent_survivors=survivors,
            )

            exp.candidate = _strip_meta(candidate)
            exp.agent_exit = 0

            # 2. Create worktree and apply diff
            wt_dir = git_worktree_add("AR")
            diff_text = candidate.get("unified_diff", "")

            if not diff_text.strip():
                exp.error = "empty unified_diff"
                exp.gate_reason = "validation: empty diff"
                failures += 1
                print("[runner]   SKIP: empty diff", flush=True)
                continue

            apply_ok, apply_err = apply_diff(wt_dir, diff_text)
            if not apply_ok:
                exp.error = f"git apply failed: {apply_err[:200]}"
                exp.gate_reason = "validation: diff does not apply"
                failures += 1
                print(f"[runner]   SKIP: diff does not apply — {apply_err[:100]}", flush=True)
                continue

            # 3. Scope check
            scope_ok, violated = scope_check(wt_dir, RECALL_ALLOWED_PATHS)
            if not scope_ok:
                exp.violated_scope = True
                exp.violated_paths = violated
                exp.gate_reason = f"validation: scope violation {violated}"
                failures += 1
                print(f"[runner]   SKIP: scope violation — {violated}", flush=True)
                continue

            # 4. Validate (syntax + imports)
            validation_result = validate_worktree(wt_dir)
            if not validation_result["syntax_ok"]:
                exp.error = f"syntax errors: {validation_result['syntax_errors']}"
                exp.gate_reason = "validation: syntax error"
                failures += 1
                print("[runner]   SKIP: syntax error", flush=True)
                continue

            if not validation_result["import_ok"]:
                exp.error = f"import error: {validation_result['import_error']}"
                exp.gate_reason = "validation: import error"
                failures += 1
                print("[runner]   SKIP: import error", flush=True)
                continue

            # 5. Run eval harness (RECALL-specific step)
            eval_metrics = run_eval(wt_dir, db_path, eval_queries_path, baseline)

            # 6. Judge scores the change (with eval metrics)
            verdict = judge.judge_recall_change(
                _strip_meta(candidate), validation_result, eval_metrics
            )
            ok, reason = _recall_gate_check(verdict)

            composite = recall_composite_score(verdict)
            exp.score = round(composite, 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.eval_breakdown = {
                "retrieval": verdict["retrieval"],
                "minimalism": verdict["minimalism"],
                "reliability": verdict["reliability"],
                "taste": verdict["taste"],
                "eval_metrics": {k: v for k, v in eval_metrics.items() if k != "per_query"},
                "reasons": {
                    "retrieval": verdict.get("retrieval_reason", ""),
                    "minimalism": verdict.get("minimalism_reason", ""),
                    "reliability": verdict.get("reliability_reason", ""),
                    "taste": verdict.get("taste_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
                "validation": validation_result,
            }

            if ok:
                _recall_promote(candidate, verdict, i, validation_result, eval_metrics)
                promoted += 1

            elapsed = time.time() - exp_started
            mrr_delta = eval_metrics.get("mrr_delta", 0.0)
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={composite:.2f} "
                f"({verdict['retrieval']}/{verdict['minimalism']}/{verdict['reliability']}/{verdict['taste']}) "
                f"mrr_delta={mrr_delta:+.4f} "
                f"change={candidate.get('change_id', '?')} "
                f"t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            if wt_dir is not None:
                git_worktree_remove(wt_dir)
            log_experiment(exp)

        # Drift check every N experiments
        if (i + 1) % DRIFT_CHECK_EVERY == 0:
            info = _recall_drift_check(i)
            if info and info.get("alarm"):
                print(
                    f"[runner] DRIFT ALARM — current best re-scored from "
                    f"{info['old_composite']} to {info['new_composite']} "
                    f"(delta {info['delta']}). Halting.",
                    flush=True,
                )
                break
            elif info and "drift_check_failed" in info:
                print(f"[runner] drift check failed: {info['drift_check_failed']}", flush=True)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


# ---------------------------------------------------------------------------
# Context-rot track
# ---------------------------------------------------------------------------

CONTEXT_ROT_PROMOTION_COMPOSITE_FLOOR = 8.0
CONTEXT_ROT_PROMOTION_MIN_AXIS = 6

# Positioning text scores vary by ~1-2 points on re-eval (normal LLM variance).
# Widen threshold to avoid halting a 90% promotion rate run.
CONTEXT_ROT_DRIFT_HALT_THRESHOLD = 2.0

_CONTEXT_ROT_MODE_SEQUENCE = [
    "define", "quantify", "narrate", "counter",
    "define", "narrate", "quantify", "counter",
]


def _rotate_context_rot_mode(i: int) -> str:
    return _CONTEXT_ROT_MODE_SEQUENCE[i % len(_CONTEXT_ROT_MODE_SEQUENCE)]


def _load_context_rot_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not CONTEXT_ROT_TARGET_JSONL.exists():
        return []
    lines = CONTEXT_ROT_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _context_rot_gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    composite = context_rot_composite_score(verdict)
    min_ax = context_rot_min_axis(verdict)

    if not verdict.get("counter_narrative_valid", False):
        return (False, "counter_narrative_valid is false")

    if min_ax < CONTEXT_ROT_PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {CONTEXT_ROT_PROMOTION_MIN_AXIS}")

    if composite < CONTEXT_ROT_PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {CONTEXT_ROT_PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {CONTEXT_ROT_PROMOTION_COMPOSITE_FLOOR}")


def _context_rot_promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
) -> dict[str, Any]:
    composite = context_rot_composite_score(verdict)
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "mode": candidate["mode"],
        "definition": candidate["definition"],
        "manifestation": candidate["manifestation"],
        "cost_model": candidate["cost_model"],
        "inevitability": candidate["inevitability"],
        "counter_narrative": candidate["counter_narrative"],
        "evidence_anchor": candidate["evidence_anchor"],
        "scores": {
            "legibility": verdict["legibility"],
            "economic": verdict["economic"],
            "inevitability": verdict["inevitability"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "legibility": verdict.get("legibility_reason", ""),
            "economic": verdict.get("economic_reason", ""),
            "inevitability": verdict.get("inevitability_reason", ""),
        },
        "counter_narrative_valid": verdict.get("counter_narrative_valid", False),
        "counter_narrative_reason": verdict.get("counter_narrative_reason", ""),
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
    }
    append_jsonl(CONTEXT_ROT_TARGET_JSONL, survivor)
    prior = load_best("context-rot")
    if composite > prior.get("composite", 0.0):
        save_best(
            "context-rot",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
            },
        )
    return survivor


def _context_rot_drift_check(experiment_i: int) -> dict[str, Any] | None:
    best = load_best("context-rot")
    if not best.get("candidate"):
        return None
    candidate = best["candidate"]
    try:
        verdict = judge.judge_context_rot(candidate)
    except judge.JudgeError as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}
    new_composite = context_rot_composite_score(verdict)
    old_composite = best.get("composite", 0.0)
    delta = abs(new_composite - old_composite)
    alarm = delta > CONTEXT_ROT_DRIFT_HALT_THRESHOLD
    info = {
        "experiment": experiment_i,
        "old_composite": round(old_composite, 3),
        "new_composite": round(new_composite, 3),
        "delta": round(delta, 3),
        "alarm": alarm,
    }
    append_jsonl(prepare.STATE_DIR / "drift.jsonl", info)
    return info


def run_context_rot_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the context-rot problem-definition loop."""
    print(f"[runner] context-rot track — max {max_experiments} experiments", flush=True)
    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_context_rot_mode(i)
        survivors = _load_context_rot_survivors(5)
        current_best = load_best("context-rot")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="context-rot",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        candidate = None
        verdict = None
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:10s} best={current_best_composite:.2f}",
                flush=True,
            )
            candidate = agent.propose_context_rot(
                mode=mode,
                recent_survivors=survivors,
            )
            verdict = judge.judge_context_rot(_strip_meta(candidate))
            ok, reason = _context_rot_gate_check(verdict)

            exp.candidate = _strip_meta(candidate)
            exp.score = round(context_rot_composite_score(verdict), 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.eval_breakdown = {
                "legibility": verdict["legibility"],
                "economic": verdict["economic"],
                "inevitability": verdict["inevitability"],
                "counter_narrative_valid": verdict.get("counter_narrative_valid", False),
                "anti_pattern_words": verdict.get("anti_pattern_words", []),
                "reasons": {
                    "legibility": verdict.get("legibility_reason", ""),
                    "economic": verdict.get("economic_reason", ""),
                    "inevitability": verdict.get("inevitability_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
            }
            exp.agent_exit = 0

            if ok:
                _context_rot_promote(candidate, verdict, i)
                promoted += 1

            elapsed = time.time() - exp_started
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={exp.score:.2f} "
                f"({verdict['legibility']}/{verdict['economic']}/{verdict['inevitability']}) "
                f"counter={'OK' if verdict.get('counter_narrative_valid') else 'FAIL'} "
                f"t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            log_experiment(exp)

        if (i + 1) % DRIFT_CHECK_EVERY == 0:
            info = _context_rot_drift_check(i)
            if info and info.get("alarm"):
                print(
                    f"[runner] DRIFT ALARM — current best re-scored from "
                    f"{info['old_composite']} to {info['new_composite']} "
                    f"(delta {info['delta']}). Halting.",
                    flush=True,
                )
                break
            elif info and "drift_check_failed" in info:
                print(f"[runner] drift check failed: {info['drift_check_failed']}", flush=True)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


# ---------------------------------------------------------------------------
# Onboard track
# ---------------------------------------------------------------------------

ONBOARD_PROMOTION_COMPOSITE_FLOOR = 7.0
ONBOARD_PROMOTION_MIN_AXIS = 6
ONBOARD_DRIFT_HALT_THRESHOLD = 1.0

# Allowed scope for onboard patches — mirrors program.md / rubric.md.
# The agent's target_file schema is constrained to the first 5; scope_check
# here also lets pre-existing legacy paths pass through if the worktree
# somehow picks them up (belt-and-suspenders).
ONBOARD_ALLOWED_PATHS = [
    "installer/setup.sh",
    "bin/cli.js",
    "hooks/post-compaction-context.sh",
    "hooks/stop.sh",
    "hooks/sync-transcript.sh",
]

# One mode per silent-failure site. Each mode constrains the agent's anchor
# region so it can't drift into F2-style branching sprees.
_ONBOARD_MODE_SEQUENCE = [
    "mcp_verify",
    "mcp_reachable",
    "token_shape",
    "restart_nudge",
    "hooks_fired",
    "discover",
]


def _rotate_onboard_mode(i: int) -> str:
    return _ONBOARD_MODE_SEQUENCE[i % len(_ONBOARD_MODE_SEQUENCE)]


def _load_onboard_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not ONBOARD_TARGET_JSONL.exists():
        return []
    lines = ONBOARD_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _onboard_gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    composite = onboard_composite_score(verdict)
    min_ax = onboard_min_axis(verdict)

    if verdict.get("scope_violation", False):
        return (False, "scope_violation true")

    if min_ax < ONBOARD_PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {ONBOARD_PROMOTION_MIN_AXIS}")

    if composite < ONBOARD_PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {ONBOARD_PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {ONBOARD_PROMOTION_COMPOSITE_FLOOR}")


def _onboard_promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
    validation_result: dict[str, Any],
    constructed_diff: str,
) -> dict[str, Any]:
    composite = onboard_composite_score(verdict)
    insertion_lines = candidate.get("insertion_lines", []) or []
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "change_id": candidate.get("change_id", ""),
        "mode": candidate.get("mode", ""),
        "target_file": candidate.get("target_file", ""),
        "anchor_line": candidate.get("anchor_line", ""),
        "insertion_lines": insertion_lines,
        "lines_added": len(insertion_lines),
        "description": candidate.get("description", ""),
        "rationale": candidate.get("rationale", ""),
        "failure_modes_addressed": candidate.get("failure_modes_addressed", []),
        "error_message": candidate.get("error_message", ""),
        "next_command": candidate.get("next_command", ""),
        "expected_impact": candidate.get("expected_impact", ""),
        "unified_diff": constructed_diff,
        "scores": {
            "guard_pattern_fidelity": verdict["guard_pattern_fidelity"],
            "time_to_value": verdict["time_to_value"],
            "failure_mode_coverage": verdict["failure_mode_coverage"],
            "error_craftsmanship": verdict["error_craftsmanship"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "guard_pattern_fidelity": verdict.get("guard_pattern_fidelity_reason", ""),
            "time_to_value": verdict.get("time_to_value_reason", ""),
            "failure_mode_coverage": verdict.get("failure_mode_coverage_reason", ""),
            "error_craftsmanship": verdict.get("error_craftsmanship_reason", ""),
        },
        "scope_violation": verdict.get("scope_violation", False),
        "scope_violation_reason": verdict.get("scope_violation_reason", ""),
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
        "validation": validation_result,
    }
    append_jsonl(ONBOARD_TARGET_JSONL, survivor)
    prior = load_best("onboard")
    if composite > prior.get("composite", 0.0):
        save_best(
            "onboard",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
                "constructed_diff": constructed_diff,
            },
        )
    return survivor


def _onboard_drift_check(experiment_i: int) -> dict[str, Any] | None:
    best = load_best("onboard")
    if not best.get("candidate"):
        return None
    candidate = best["candidate"]
    # Re-run validation. For the new insertion_block schema, we rebuild the
    # diff from anchor+insertion; for legacy survivors (pre-rewrite) we fall
    # back to the stored unified_diff.
    wt = None
    try:
        wt = git_worktree_add("AR")
        diff_text = ""
        if candidate.get("anchor_line") and candidate.get("insertion_lines"):
            ok, diff_text, err = construct_insertion_diff(
                wt,
                candidate.get("target_file", ""),
                candidate["anchor_line"],
                candidate["insertion_lines"],
            )
            if not ok:
                return {"drift_check_failed": f"construct_insertion_diff: {err}", "experiment": experiment_i}
        else:
            diff_text = best.get("constructed_diff", "") or candidate.get("unified_diff", "")
        apply_ok, apply_err = apply_diff(wt, diff_text)
        if not apply_ok:
            return {"drift_check_failed": f"apply_diff: {apply_err[:200]}", "experiment": experiment_i}
        validation_result = validate_onboard_worktree(wt)
        validation_result["constructed_diff"] = diff_text
    except Exception as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}
    finally:
        if wt is not None:
            git_worktree_remove(wt)

    try:
        verdict = judge.judge_onboard_change(candidate, validation_result)
    except judge.JudgeError as e:
        return {"drift_check_failed": str(e), "experiment": experiment_i}

    new_composite = onboard_composite_score(verdict)
    old_composite = best.get("composite", 0.0)
    delta = abs(new_composite - old_composite)
    alarm = delta > ONBOARD_DRIFT_HALT_THRESHOLD
    info = {
        "experiment": experiment_i,
        "old_composite": round(old_composite, 3),
        "new_composite": round(new_composite, 3),
        "delta": round(delta, 3),
        "alarm": alarm,
    }
    append_jsonl(prepare.STATE_DIR / "drift.jsonl", info)
    return info


def run_onboard_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the onboard install-surface improvement loop."""
    print(f"[runner] onboard track — max {max_experiments} experiments", flush=True)
    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_onboard_mode(i)
        survivors = _load_onboard_survivors(5)
        current_best = load_best("onboard")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="onboard",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        candidate = None
        verdict = None
        wt_dir = None
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:14s} best={current_best_composite:.2f}",
                flush=True,
            )

            candidate = agent.propose_onboard_change(
                mode=mode,
                recent_survivors=survivors,
            )

            target_file = candidate.get("target_file", "")
            anchor_line = candidate.get("anchor_line", "")
            insertion_lines = candidate.get("insertion_lines", []) or []

            # Build the diff deterministically from anchor + insertion. Anchor
            # must match exactly once; missing/ambiguous anchors fail here,
            # not at git-apply time.
            wt_dir = git_worktree_add("AR")
            build_ok, diff_text, build_err = construct_insertion_diff(
                wt_dir, target_file, anchor_line, insertion_lines
            )

            if not build_ok:
                exp.candidate = _strip_meta(candidate)
                exp.agent_exit = 0
                exp.error = f"anchor resolution failed: {build_err[:300]}"
                exp.eval_breakdown = {
                    "anchor_ok": False,
                    "anchor_error": build_err[:500],
                    "target_file": target_file,
                    "anchor_line": anchor_line[:120],
                    "agent_usage": candidate.get("_usage", {}),
                }
                print(f"[runner]   ANCHOR FAILED: {build_err[:160]}", flush=True)
                failures += 1
                continue

            apply_ok, apply_err = apply_diff(wt_dir, diff_text)
            if not apply_ok:
                exp.candidate = _strip_meta(candidate)
                exp.agent_exit = 0
                exp.error = f"apply_diff failed: {apply_err[:400]}"
                exp.eval_breakdown = {
                    "anchor_ok": True,
                    "apply_ok": False,
                    "apply_error": apply_err[:600],
                    "constructed_diff": diff_text,
                    "agent_usage": candidate.get("_usage", {}),
                }
                print(f"[runner]   APPLY FAILED: {apply_err[:160]}", flush=True)
                failures += 1
                continue

            # Scope check + syntax validation.
            scope_ok, violated = scope_check(wt_dir, ONBOARD_ALLOWED_PATHS)
            validation_result = validate_onboard_worktree(wt_dir)
            validation_result["scope_ok"] = scope_ok
            validation_result["violated_paths"] = violated
            validation_result["constructed_diff"] = diff_text
            validation_result["anchor_ok"] = True

            if not validation_result["syntax_ok"]:
                exp.candidate = _strip_meta(candidate)
                exp.agent_exit = 0
                exp.violated_scope = not scope_ok
                exp.violated_paths = violated
                exp.error = "syntax_check_failed"
                exp.eval_breakdown = {
                    "anchor_ok": True,
                    "apply_ok": True,
                    "scope_ok": scope_ok,
                    "violated_paths": violated,
                    "syntax_errors": validation_result.get("syntax_errors", []),
                    "constructed_diff": diff_text,
                    "agent_usage": candidate.get("_usage", {}),
                }
                print(
                    f"[runner]   SYNTAX FAILED: {validation_result.get('syntax_errors', [])[:2]}",
                    flush=True,
                )
                failures += 1
                continue

            verdict = judge.judge_onboard_change(
                _strip_meta(candidate),
                validation_result,
            )
            ok, reason = _onboard_gate_check(verdict)

            exp.candidate = _strip_meta(candidate)
            exp.score = round(onboard_composite_score(verdict), 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.violated_scope = not scope_ok
            exp.violated_paths = violated
            exp.eval_breakdown = {
                "guard_pattern_fidelity": verdict["guard_pattern_fidelity"],
                "time_to_value": verdict["time_to_value"],
                "failure_mode_coverage": verdict["failure_mode_coverage"],
                "error_craftsmanship": verdict["error_craftsmanship"],
                "scope_violation": verdict.get("scope_violation", False),
                "anti_pattern_words": verdict.get("anti_pattern_words", []),
                "validation": validation_result,
                "reasons": {
                    "guard_pattern_fidelity": verdict.get("guard_pattern_fidelity_reason", ""),
                    "time_to_value": verdict.get("time_to_value_reason", ""),
                    "failure_mode_coverage": verdict.get("failure_mode_coverage_reason", ""),
                    "error_craftsmanship": verdict.get("error_craftsmanship_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
            }
            exp.agent_exit = 0

            if ok:
                _onboard_promote(candidate, verdict, i, validation_result, diff_text)
                save_patch(
                    i,
                    candidate.get("change_id", "onboard"),
                    diff_text,
                    track_dir=prepare.ONBOARD_DIR,
                )
                promoted += 1

            elapsed = time.time() - exp_started
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={exp.score:.2f} "
                f"(gpf={verdict['guard_pattern_fidelity']}/ttv={verdict['time_to_value']}/"
                f"fmc={verdict['failure_mode_coverage']}/ec={verdict['error_craftsmanship']}) "
                f"scope_ok={scope_ok} t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            if wt_dir is not None:
                git_worktree_remove(wt_dir)
            log_experiment(exp)

        if (i + 1) % DRIFT_CHECK_EVERY == 0:
            info = _onboard_drift_check(i)
            if info and info.get("alarm"):
                print(
                    f"[runner] DRIFT ALARM — current best re-scored from "
                    f"{info['old_composite']} to {info['new_composite']} "
                    f"(delta {info['delta']}). Halting.",
                    flush=True,
                )
                break
            elif info and "drift_check_failed" in info:
                print(f"[runner] drift check failed: {info['drift_check_failed']}", flush=True)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


# ---------------------------------------------------------------------------
# Distribution track
# ---------------------------------------------------------------------------

DISTRIBUTION_PROMOTION_COMPOSITE_FLOOR = 8.0
DISTRIBUTION_PROMOTION_MIN_AXIS = 6


def _rotate_distribution_mode(i: int) -> str:
    """Alternate refine/discover. Even = refine, odd = discover."""
    return "refine" if i % 2 == 0 else "discover"


def _load_distribution_survivors(n: int = 5) -> list[dict[str, Any]]:
    if not DISTRIBUTION_TARGET_JSONL.exists():
        return []
    lines = DISTRIBUTION_TARGET_JSONL.read_text().splitlines()
    survivors: list[dict[str, Any]] = []
    for line in lines[-n * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            survivors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return survivors[-n:]


def _distribution_gate_check(verdict: dict[str, Any]) -> tuple[bool, str]:
    composite = distribution_composite_score(verdict)
    min_ax = distribution_min_axis(verdict)

    if verdict.get("constraint_violation", False):
        return (False, "constraint_violation=true")

    if min_ax < DISTRIBUTION_PROMOTION_MIN_AXIS:
        return (False, f"min_axis {min_ax} < {DISTRIBUTION_PROMOTION_MIN_AXIS}")

    if composite < DISTRIBUTION_PROMOTION_COMPOSITE_FLOOR:
        return (False, f"composite {composite:.2f} < floor {DISTRIBUTION_PROMOTION_COMPOSITE_FLOOR}")

    return (True, f"composite {composite:.2f} clears floor {DISTRIBUTION_PROMOTION_COMPOSITE_FLOOR}")


def _distribution_promote(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
    experiment_i: int,
) -> dict[str, Any]:
    composite = distribution_composite_score(verdict)
    survivor = {
        "experiment": experiment_i,
        "ts": now(),
        "mode": candidate["mode"],
        "demo_mechanism": candidate["demo_mechanism"],
        "business_model": candidate["business_model"],
        "pricing": candidate["pricing"],
        "most_effective_demo": candidate["most_effective_demo"],
        "followership_channel": candidate["followership_channel"],
        "precedent": candidate["precedent"],
        "reasoning": candidate["reasoning"],
        "scores": {
            "feasibility": verdict["feasibility"],
            "differentiation": verdict["differentiation"],
            "coherence": verdict["coherence"],
            "composite": round(composite, 3),
        },
        "score_reasons": {
            "feasibility": verdict.get("feasibility_reason", ""),
            "differentiation": verdict.get("differentiation_reason", ""),
            "coherence": verdict.get("coherence_reason", ""),
        },
        "constraint_violation": verdict.get("constraint_violation", False),
        "constraint_violation_reason": verdict.get("constraint_violation_reason", ""),
        "anti_pattern_words": verdict.get("anti_pattern_words", []),
    }
    append_jsonl(DISTRIBUTION_TARGET_JSONL, survivor)
    prior = load_best("distribution")
    if composite > prior.get("composite", 0.0):
        save_best(
            "distribution",
            {
                "composite": round(composite, 3),
                "experiment": experiment_i,
                "ts": survivor["ts"],
                "candidate": _strip_meta(candidate),
                "verdict": _strip_meta(verdict),
            },
        )
    return survivor


def run_distribution_loop(max_experiments: int, max_wall_s: int | None) -> int:
    """Run the distribution loop. Returns number of experiments executed."""
    print(f"[runner] distribution track — max {max_experiments} experiments", flush=True)
    start_ts = time.time()
    executed = 0
    promoted = 0
    failures = 0

    for i in range(max_experiments):
        if _SHUTDOWN_REQUESTED:
            print("[runner] shutdown requested — stopping loop", flush=True)
            break
        if max_wall_s is not None and (time.time() - start_ts) > max_wall_s:
            print(f"[runner] wall-clock budget {max_wall_s}s exceeded — stopping", flush=True)
            break

        executed += 1
        mode = _rotate_distribution_mode(i)
        survivors = _load_distribution_survivors(5)
        current_best = load_best("distribution")
        current_best_composite = current_best.get("composite", 0.0)

        exp = Experiment(
            ts=now(),
            track="distribution",
            experiment=i,
            mode=mode,
        )

        exp_started = time.time()
        try:
            print(
                f"[runner] exp {i:03d} mode={mode:8s} best={current_best_composite:.2f}",
                flush=True,
            )
            candidate = agent.propose_distribution_plan(
                mode=mode,
                recent_survivors=survivors,
            )
            verdict = judge.judge_distribution_candidate(_strip_meta(candidate))
            ok, reason = _distribution_gate_check(verdict)

            exp.candidate = _strip_meta(candidate)
            exp.score = round(distribution_composite_score(verdict), 3)
            exp.best_before = round(current_best_composite, 3)
            exp.promoted = ok
            exp.gate_reason = reason
            exp.eval_breakdown = {
                "feasibility": verdict["feasibility"],
                "differentiation": verdict["differentiation"],
                "coherence": verdict["coherence"],
                "constraint_violation": verdict.get("constraint_violation", False),
                "anti_pattern_words": verdict.get("anti_pattern_words", []),
                "reasons": {
                    "feasibility": verdict.get("feasibility_reason", ""),
                    "differentiation": verdict.get("differentiation_reason", ""),
                    "coherence": verdict.get("coherence_reason", ""),
                    "constraint_violation": verdict.get("constraint_violation_reason", ""),
                },
                "agent_usage": candidate.get("_usage", {}),
                "judge_usage": verdict.get("_usage", {}),
            }
            exp.agent_exit = 0

            if ok:
                _distribution_promote(candidate, verdict, i)
                promoted += 1

            elapsed = time.time() - exp_started
            tag = "[PROMOTED]" if ok else "  (discard)"
            print(
                f"[runner]   {tag} score={exp.score:.2f} "
                f"(feas={verdict['feasibility']}/diff={verdict['differentiation']}/coh={verdict['coherence']}) "
                f"cv={'YES' if verdict.get('constraint_violation') else 'no'} "
                f"t={elapsed:.1f}s — {reason}",
                flush=True,
            )

        except Exception as e:
            failures += 1
            exp.error = f"{type(e).__name__}: {e}"
            exp.agent_exit = 1
            print(f"[runner]   ERROR: {exp.error}", flush=True)
        finally:
            log_experiment(exp)

    total_elapsed = time.time() - start_ts
    print(
        f"\n[runner] done. {executed} experiments, {promoted} promoted, "
        f"{failures} failures, {total_elapsed:.0f}s total",
        flush=True,
    )
    return executed


def main() -> int:
    ap = argparse.ArgumentParser(prog="autoresearch.runner")
    ap.add_argument("--track", choices=["moat", "code", "recall", "context-rot", "onboard", "distribution"], default="moat",
                    help="Which track to run (moat=positioning, code=engine improvements, recall=search quality, context-rot=problem naming, onboard=install surface, distribution=launch plans).")
    ap.add_argument("--max-experiments", type=int, default=80,
                    help="Max experiments before stopping. Default 80.")
    ap.add_argument("--max-wall-s", type=int, default=None,
                    help="Max wall-clock seconds before stopping.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run safety preflight, print state, exit. No API calls.")
    ap.add_argument("--release-lock", action="store_true",
                    help="Force-clear state/lock. Use when a previous run crashed.")
    args = ap.parse_args()

    if args.release_lock:
        prepare.release_lock(force=True)
        print("[runner] lock released.", flush=True)
        return 0

    if args.dry_run:
        prepare.dry_run(track=args.track)
        return 0

    _install_signal_handlers()

    try:
        report = prepare.verify_safety(track=args.track)
    except prepare.SafetyError as e:
        print(f"[runner] SAFETY FAILURE: {e}", file=sys.stderr)
        return 1

    print(
        f"[runner] safety OK — branch={report.branch} pid={report.lock_pid}",
        flush=True,
    )
    print(
        f"[runner] ground-truth posts: "
        f"{', '.join(h[:8] for h in report.ground_truth_hashes.values())}",
        flush=True,
    )
    print(f"[runner] rubric: {report.rubric_hash[:16]}", flush=True)

    try:
        if args.track == "moat":
            run_moat_loop(args.max_experiments, args.max_wall_s)
        elif args.track == "code":
            run_code_loop(args.max_experiments, args.max_wall_s)
        elif args.track == "recall":
            run_recall_loop(args.max_experiments, args.max_wall_s)
        elif args.track == "context-rot":
            run_context_rot_loop(args.max_experiments, args.max_wall_s)
        elif args.track == "onboard":
            run_onboard_loop(args.max_experiments, args.max_wall_s)
        elif args.track == "distribution":
            run_distribution_loop(args.max_experiments, args.max_wall_s)
    finally:
        prepare.release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
