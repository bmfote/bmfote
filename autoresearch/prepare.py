"""
Autoresearch harness — safety checks and setup. FIXED FILE, never edited by the agent.

Responsibilities, in strict order:
1. Branch guard: refuse to run unless HEAD is `AR`. main/master are blocked by name.
2. Remote-DB blocker: CCTX_REMOTE_DB must be unset. Refuse if set.
3. Ground-truth hash check: verify the three moat posts + rubric haven't drifted.
4. Lock management: claim state/lock with current PID, release on exit.

Called by runner.py before every experiment. Any failure halts the run.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
STATE_DIR = HARNESS_DIR / "state"
BEST_DIR = STATE_DIR / "best"
LOCK_FILE = STATE_DIR / "lock"
EXPERIMENTS_LOG = STATE_DIR / "experiments.jsonl"

MOAT_DIR = HARNESS_DIR / "tracks" / "moat"
MOAT_GROUND_TRUTH = MOAT_DIR / "ground_truth"
MOAT_RUBRIC = MOAT_DIR / "rubric.md"

CODE_DIR = HARNESS_DIR / "tracks" / "code"
CODE_GROUND_TRUTH = CODE_DIR / "ground_truth"
CODE_RUBRIC = CODE_DIR / "rubric.md"

RECALL_DIR = HARNESS_DIR / "tracks" / "recall"
RECALL_GROUND_TRUTH = RECALL_DIR / "ground_truth"
RECALL_RUBRIC = RECALL_DIR / "rubric.md"

CONTEXT_ROT_DIR = HARNESS_DIR / "tracks" / "context-rot"
CONTEXT_ROT_GROUND_TRUTH = CONTEXT_ROT_DIR / "ground_truth"
CONTEXT_ROT_RUBRIC = CONTEXT_ROT_DIR / "rubric.md"

ALLOWED_BRANCH = "AR"
BLOCKED_BRANCHES = {"main", "master"}


class SafetyError(RuntimeError):
    """Raised when any safety precondition fails. Halts the run."""


@dataclass
class SafetyReport:
    branch: str
    ground_truth_hashes: dict[str, str]
    rubric_hash: str
    lock_pid: int


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    out = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise SafetyError(f"command failed: {' '.join(cmd)}\n{out.stderr}")
    return out.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_branch() -> str:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch in BLOCKED_BRANCHES:
        raise SafetyError(
            f"refusing to run on blocked branch '{branch}'. "
            f"autoresearch only runs on '{ALLOWED_BRANCH}'."
        )
    if branch != ALLOWED_BRANCH:
        raise SafetyError(
            f"wrong branch: HEAD is '{branch}', expected '{ALLOWED_BRANCH}'. "
            f"run `git switch {ALLOWED_BRANCH}` first."
        )
    return branch


def check_remote_db_blocker() -> None:
    if os.environ.get("CCTX_REMOTE_DB"):
        raise SafetyError(
            "CCTX_REMOTE_DB is set in environment — harness refuses to run. "
            "unset it before launching: `unset CCTX_REMOTE_DB`."
        )
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        raise SafetyError(
            "RAILWAY_ENVIRONMENT is set — you appear to be inside a Railway container. "
            "autoresearch only runs on local dev machines."
        )


def load_ground_truth_hashes(track: str = "moat") -> dict[str, str]:
    """Hash the ground-truth files for the given track. Editing any resets best/{track}.json."""
    if track == "moat":
        expected_files = [
            "post_1_minimalism.md",
            "post_2_cloud_context.md",
            "post_3_shared_brain.md",
        ]
        gt_dir = MOAT_GROUND_TRUTH
    elif track == "code":
        expected_files = [
            "post_1_minimalism.md",
            "post_2_cloud_context.md",
            "post_3_shared_brain.md",
            "post_4_memory_moat.md",
            "audit.md",
            "reference_context_os.md",
        ]
        gt_dir = CODE_GROUND_TRUTH
    elif track == "recall":
        expected_files = [
            "post_1_minimalism.md",
            "post_2_cloud_context.md",
            "eval_queries.jsonl",
            "search_analysis.md",
        ]
        gt_dir = RECALL_GROUND_TRUTH
    elif track == "context-rot":
        expected_files = [
            "post_1_minimalism.md",
            "post_2_cloud_context.md",
            "post_3_shared_brain.md",
            "evidence.md",
            "problem_definition.md",
        ]
        gt_dir = CONTEXT_ROT_GROUND_TRUTH
    else:
        raise SafetyError(f"unknown track: {track}")
    hashes: dict[str, str] = {}
    for name in expected_files:
        path = gt_dir / name
        if not path.exists():
            raise SafetyError(
                f"missing ground-truth file: {path}. "
                f"cannot run {track} track without frozen files."
            )
        hashes[name] = _sha256(path)
    return hashes


def load_rubric_hash(track: str = "moat") -> str:
    rubric = {"code": CODE_RUBRIC, "recall": RECALL_RUBRIC, "context-rot": CONTEXT_ROT_RUBRIC}.get(track, MOAT_RUBRIC)
    if not rubric.exists():
        raise SafetyError(f"missing {track} rubric: {rubric}")
    return _sha256(rubric)


def claim_lock() -> int:
    """Write current PID to state/lock. Refuses if a live PID already holds it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BEST_DIR.mkdir(parents=True, exist_ok=True)

    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            LOCK_FILE.unlink(missing_ok=True)
        else:
            if _pid_alive(existing_pid):
                raise SafetyError(
                    f"lock held by live pid {existing_pid}. "
                    "use `python -m autoresearch.runner --release-lock` to clear "
                    "if you're sure no run is active."
                )
            # stale lock — clear it
            LOCK_FILE.unlink(missing_ok=True)

    pid = os.getpid()
    LOCK_FILE.write_text(str(pid))
    return pid


def release_lock(force: bool = False) -> None:
    """Remove the lock file. If `force`, also prune stale worktrees."""
    if LOCK_FILE.exists():
        try:
            held_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            held_pid = -1
        if force or held_pid == os.getpid() or not _pid_alive(held_pid):
            LOCK_FILE.unlink(missing_ok=True)
    if force:
        try:
            _run(["git", "worktree", "prune"])
        except SafetyError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def verify_safety(track: str = "moat") -> SafetyReport:
    """Full preflight. Call this before every experiment. Raises SafetyError on failure."""
    branch = check_branch()
    check_remote_db_blocker()
    gt_hashes = load_ground_truth_hashes(track)
    rubric_hash = load_rubric_hash(track)
    pid = claim_lock()
    return SafetyReport(
        branch=branch,
        ground_truth_hashes=gt_hashes,
        rubric_hash=rubric_hash,
        lock_pid=pid,
    )


def dry_run(track: str = "moat") -> None:
    """Entry point for `runner.py --dry-run`. Prints state, exits clean."""
    try:
        report = verify_safety(track)
    except SafetyError as e:
        print(f"SAFETY FAILURE: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # dry run releases the lock immediately
        release_lock()
    print(f"branch:         {report.branch}")
    print(f"lock pid:       {report.lock_pid} (released)")
    print("ground truth:")
    for name, h in report.ground_truth_hashes.items():
        print(f"  {name}: {h[:16]}")
    print(f"rubric.md:      {report.rubric_hash[:16]}")
    print("\nOK. safety preflight passed.")


if __name__ == "__main__":
    dry_run()
