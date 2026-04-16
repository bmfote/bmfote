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
    git_worktree_add,
    git_worktree_remove,
    load_best,
    log_experiment,
    now,
    save_best,
    save_patch,
    scope_check,
    validate_worktree,
)
from autoresearch.judge import (
    code_composite_score,
    code_min_axis,
    composite_score,
    min_axis,
)

MOAT_TARGET_JSONL = prepare.MOAT_DIR / "target.jsonl"
CODE_TARGET_JSONL = prepare.CODE_DIR / "target.jsonl"

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


def main() -> int:
    ap = argparse.ArgumentParser(prog="autoresearch.runner")
    ap.add_argument("--track", choices=["moat", "code"], default="moat",
                    help="Which track to run (moat=positioning, code=engine improvements).")
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
    finally:
        prepare.release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
