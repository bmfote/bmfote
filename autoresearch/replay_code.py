#!/usr/bin/env python3
"""Replay code-track experiments: re-validate and re-judge saved candidates.

Reads candidates from state/experiments.jsonl, applies each diff in a fresh
worktree, validates, and (if validation passes) calls the judge. Results are
written to a NEW file: state/replay_results.jsonl — the original experiments
log is not modified.

Usage:
    .venv/bin/python -m autoresearch.replay_code
    .venv/bin/python -m autoresearch.replay_code --max 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from autoresearch import judge, prepare
from autoresearch.eval_common import (
    append_jsonl,
    apply_diff,
    git_worktree_add,
    git_worktree_remove,
    now,
    save_best,
    save_patch,
    scope_check,
    validate_worktree,
    load_best,
)
from autoresearch.judge import code_composite_score, code_min_axis

STATE_DIR = prepare.STATE_DIR
EXPERIMENTS_LOG = prepare.EXPERIMENTS_LOG
REPLAY_LOG = STATE_DIR / "replay_results.jsonl"
CODE_TARGET_JSONL = prepare.CODE_DIR / "target.jsonl"
CODE_ALLOWED_PATHS = ["engine/"]

PROMOTION_COMPOSITE_FLOOR = 7.5
PROMOTION_MIN_AXIS = 5


def load_code_candidates() -> list[dict]:
    """Load all code-track experiment records that have a candidate with a diff."""
    if not EXPERIMENTS_LOG.exists():
        return []
    candidates = []
    for line in EXPERIMENTS_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            exp = json.loads(line)
        except json.JSONDecodeError:
            continue
        if exp.get("track") != "code":
            continue
        candidate = exp.get("candidate")
        if not candidate or not candidate.get("unified_diff", "").strip():
            continue
        candidates.append(exp)
    return candidates


def replay_one(exp: dict) -> dict:
    """Re-validate and re-judge one candidate. Returns a result dict."""
    candidate = exp["candidate"]
    diff_text = candidate.get("unified_diff", "")
    orig_exp = exp.get("experiment", -1)
    issue_id = candidate.get("issue_id", "unknown")

    result = {
        "original_experiment": orig_exp,
        "issue_id": issue_id,
        "mode": exp.get("mode"),
        "ts": now(),
    }

    wt_dir = None
    try:
        wt_dir = git_worktree_add("AR")

        # Apply diff
        apply_ok, apply_err = apply_diff(wt_dir, diff_text)
        if not apply_ok:
            result["status"] = "diff_failed"
            result["error"] = apply_err[:200]
            return result

        # Scope check
        scope_ok, violated = scope_check(wt_dir, CODE_ALLOWED_PATHS)
        if not scope_ok:
            result["status"] = "scope_violation"
            result["violated_paths"] = violated
            return result

        # Validate
        validation = validate_worktree(wt_dir)
        if not validation["syntax_ok"]:
            result["status"] = "syntax_error"
            result["syntax_errors"] = validation["syntax_errors"]
            return result
        if not validation["import_ok"]:
            result["status"] = "import_error"
            result["import_error"] = validation["import_error"]
            return result

        result["validation"] = validation

        # Judge
        verdict = judge.judge_code_change(candidate, validation)
        composite = code_composite_score(verdict)
        min_ax = code_min_axis(verdict)

        result["status"] = "scored"
        result["scores"] = {
            "correctness": verdict["correctness"],
            "minimalism": verdict["minimalism"],
            "reliability": verdict["reliability"],
            "taste": verdict["taste"],
            "composite": round(composite, 3),
        }
        result["score_reasons"] = {
            "correctness": verdict.get("correctness_reason", ""),
            "minimalism": verdict.get("minimalism_reason", ""),
            "reliability": verdict.get("reliability_reason", ""),
            "taste": verdict.get("taste_reason", ""),
        }
        result["anti_pattern_words"] = verdict.get("anti_pattern_words", [])
        result["judge_usage"] = verdict.get("_usage", {})

        # Promotion check
        promoted = composite >= PROMOTION_COMPOSITE_FLOOR and min_ax >= PROMOTION_MIN_AXIS
        result["promoted"] = promoted

        if promoted:
            survivor = {
                "experiment": orig_exp,
                "ts": now(),
                "issue_id": issue_id,
                "severity": candidate.get("severity", "unknown"),
                "target_file": candidate.get("target_file", ""),
                "description": candidate.get("description", ""),
                "rationale": candidate.get("rationale", ""),
                "files_touched": candidate.get("files_touched", []),
                "lines_added": candidate.get("lines_added", 0),
                "lines_removed": candidate.get("lines_removed", 0),
                "unified_diff": diff_text,
                "scores": result["scores"],
                "score_reasons": result["score_reasons"],
                "anti_pattern_words": result["anti_pattern_words"],
                "validation": validation,
            }
            append_jsonl(CODE_TARGET_JSONL, survivor)
            save_patch(
                experiment_i=orig_exp,
                issue_id=issue_id,
                diff_text=diff_text,
            )
            prior = load_best("code")
            if composite > prior.get("composite", 0.0):
                save_best("code", {
                    "composite": round(composite, 3),
                    "experiment": orig_exp,
                    "ts": now(),
                    "candidate": candidate,
                    "verdict": {k: v for k, v in verdict.items() if not k.startswith("_")},
                })

        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    finally:
        if wt_dir is not None:
            git_worktree_remove(wt_dir)


def load_replayed_ids() -> set[int]:
    """Return experiment IDs already in replay_results.jsonl."""
    if not REPLAY_LOG.exists():
        return set()
    ids = set()
    for line in REPLAY_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line).get("original_experiment"))
        except json.JSONDecodeError:
            continue
    return ids


def main():
    ap = argparse.ArgumentParser(prog="autoresearch.replay_code")
    ap.add_argument("--max", type=int, default=None, help="Max candidates to replay")
    args = ap.parse_args()

    # Safety: must be on AR branch
    branch = prepare.check_branch()
    print(f"[replay] branch={branch}", flush=True)

    candidates = load_code_candidates()
    print(f"[replay] {len(candidates)} candidates with diffs found", flush=True)

    # Skip already-replayed experiments
    done_ids = load_replayed_ids()
    candidates = [c for c in candidates if c.get("experiment") not in done_ids]
    print(f"[replay] {len(done_ids)} already replayed, {len(candidates)} remaining", flush=True)

    if args.max:
        candidates = candidates[:args.max]

    promoted = 0
    scored = 0
    failed = 0

    for i, exp in enumerate(candidates):
        issue_id = (exp.get("candidate") or {}).get("issue_id", "?")
        print(f"[replay] {i+1}/{len(candidates)} exp={exp.get('experiment')} issue={issue_id}", end=" ", flush=True)

        result = replay_one(exp)
        append_jsonl(REPLAY_LOG, result)

        status = result["status"]
        if status == "scored":
            scored += 1
            scores = result["scores"]
            tag = "[PROMOTED]" if result.get("promoted") else "(discard)"
            print(
                f"{tag} {scores['composite']:.2f} "
                f"({scores['correctness']}/{scores['minimalism']}/{scores['reliability']}/{scores['taste']})",
                flush=True,
            )
            if result.get("promoted"):
                promoted += 1
        else:
            failed += 1
            err = result.get("error", result.get("syntax_errors", status))
            print(f"SKIP: {status} — {str(err)[:80]}", flush=True)

    print(f"\n[replay] done. {scored} scored, {promoted} promoted, {failed} failed", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
