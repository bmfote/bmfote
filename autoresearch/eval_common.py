"""
Shared infrastructure for the autoresearch harness.

Exports:
- append_jsonl: append-only JSONL writer with fsync
- load_best / save_best: per-track best.json load/save
- git_worktree_add / git_worktree_remove: worktree lifecycle
- scope_check: verify the agent only edited whitelisted paths
- Experiment: dataclass for one experiment record
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoresearch.prepare import HARNESS_DIR, REPO_ROOT, STATE_DIR, BEST_DIR, EXPERIMENTS_LOG


@dataclass
class Experiment:
    ts: float
    track: str
    experiment: int
    mode: str | None = None
    score: float | None = None
    best_before: float | None = None
    promoted: bool = False
    gate_reason: str = ""
    eval_breakdown: dict[str, Any] = field(default_factory=dict)
    agent_exit: int | None = None
    agent_stdout_tail: str = ""
    violated_scope: bool = False
    violated_paths: list[str] = field(default_factory=list)
    error: str | None = None
    candidate: dict[str, Any] | None = None


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """Append one JSON line, flush and fsync. Safe across kills."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, separators=(",", ":"), default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def log_experiment(exp: Experiment) -> None:
    append_jsonl(EXPERIMENTS_LOG, asdict(exp))


def load_best(track: str) -> dict[str, Any]:
    path = BEST_DIR / f"{track}.json"
    if not path.exists():
        return {"score": 0.0, "composite": 0.0, "ts": None, "candidate": None}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"score": 0.0, "composite": 0.0, "ts": None, "candidate": None}


def save_best(track: str, obj: dict[str, Any]) -> None:
    BEST_DIR.mkdir(parents=True, exist_ok=True)
    path = BEST_DIR / f"{track}.json"
    path.write_text(json.dumps(obj, indent=2, default=str))


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def git_worktree_add(branch: str = "AR") -> Path:
    """Create a disposable worktree in /tmp for one experiment. Returns the path."""
    wt_dir = Path(tempfile.mkdtemp(prefix="bmfote-ar-"))
    wt_dir.rmdir()  # mkdtemp creates it; git worktree add wants the path to not exist
    _run(["git", "worktree", "add", "--detach", str(wt_dir), branch])
    return wt_dir


def git_worktree_remove(wt_dir: Path) -> None:
    """Always-call cleanup. Ignores errors so a crashed worktree doesn't break the loop."""
    try:
        _run(["git", "worktree", "remove", "--force", str(wt_dir)], check=False)
    except Exception:
        pass
    # belt and suspenders — if the dir still exists, blast it
    if wt_dir.exists():
        try:
            _run(["rm", "-rf", str(wt_dir)], check=False)
        except Exception:
            pass


def git_diff_names(wt_dir: Path) -> list[str]:
    """List of files changed vs HEAD in the worktree. Used by scope_check."""
    out = _run(["git", "-C", str(wt_dir), "diff", "--name-only", "HEAD"], check=False)
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    untracked = _run(
        ["git", "-C", str(wt_dir), "ls-files", "--others", "--exclude-standard"],
        check=False,
    )
    lines += [line.strip() for line in untracked.stdout.splitlines() if line.strip()]
    return lines


def scope_check(wt_dir: Path, allowed_paths: list[str]) -> tuple[bool, list[str]]:
    """
    Returns (ok, violated_paths).
    ok=True if every changed file is under one of `allowed_paths`.
    violated_paths is the list of files outside the scope (empty if ok).
    """
    changed = git_diff_names(wt_dir)
    violated = []
    for path in changed:
        if not any(path == a or path.startswith(a + "/") or path.startswith(a) for a in allowed_paths):
            violated.append(path)
    return (len(violated) == 0, violated)


def read_candidate(wt_dir: Path, track: str) -> dict[str, Any] | None:
    """Read the mutable target file the agent filled in. Moat uses candidate.json."""
    path = wt_dir / "autoresearch" / "tracks" / track / "candidate.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def apply_diff(wt_dir: Path, diff_text: str) -> tuple[bool, str]:
    """Apply a unified diff to a worktree. Returns (success, error_message)."""
    patch_file = wt_dir / "_tmp_patch.diff"
    try:
        patch_file.write_text(diff_text, encoding="utf-8")
        # dry-run check first
        check = _run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=wt_dir,
            check=False,
        )
        if check.returncode != 0:
            return (False, check.stderr.strip())
        # apply for real
        result = _run(
            ["git", "apply", str(patch_file)],
            cwd=wt_dir,
            check=False,
        )
        if result.returncode != 0:
            return (False, result.stderr.strip())
        return (True, "")
    finally:
        patch_file.unlink(missing_ok=True)


def validate_worktree(
    wt_dir: Path,
    engine_modules: list[str] | None = None,
) -> dict:
    """Syntax-check changed .py files and verify core engine imports."""
    if engine_modules is None:
        engine_modules = ["engine.server", "engine.db", "engine.mcp_server"]

    changed = git_diff_names(wt_dir)
    py_files = [f for f in changed if f.endswith(".py")]

    syntax_ok = True
    syntax_errors: list[str] = []
    for py in py_files:
        full = wt_dir / py
        if not full.exists():
            continue
        r = _run(["python", "-m", "py_compile", str(full)], cwd=wt_dir, check=False)
        if r.returncode != 0:
            syntax_ok = False
            syntax_errors.append(f"{py}: {r.stderr.strip()}")

    import_cmd = "; ".join(f"import {m}" for m in engine_modules)
    env = {**os.environ, "PYTHONPATH": str(wt_dir)}
    imp = subprocess.run(
        ["python", "-c", import_cmd],
        cwd=wt_dir,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    import_ok = imp.returncode == 0
    import_error = imp.stderr.strip() if not import_ok else None

    return {
        "syntax_ok": syntax_ok,
        "import_ok": import_ok,
        "syntax_errors": syntax_errors,
        "import_error": import_error,
    }


def save_patch(
    experiment_i: int,
    issue_id: str,
    diff_text: str,
    track_dir: Path | None = None,
) -> Path:
    """Write a patch file to the code track's patches directory."""
    if track_dir is None:
        track_dir = HARNESS_DIR / "tracks" / "code"
    patches_dir = track_dir / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patches_dir / f"{issue_id}_{experiment_i:03d}.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    return patch_path


def now() -> float:
    return time.time()
