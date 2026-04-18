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
import sys
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
        r = _run([sys.executable, "-m", "py_compile", str(full)], cwd=wt_dir, check=False)
        if r.returncode != 0:
            syntax_ok = False
            syntax_errors.append(f"{py}: {r.stderr.strip()}")

    import_cmd = "; ".join(f"import {m}" for m in engine_modules)
    env = {**os.environ, "PYTHONPATH": str(wt_dir)}
    imp = subprocess.run(
        [sys.executable, "-c", import_cmd],
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


def construct_insertion_diff(
    wt_dir: Path,
    target_file: str,
    anchor_line: str,
    insertion_lines: list[str],
    context: int = 3,
) -> tuple[bool, str, str]:
    """Deterministically build a unified diff for an insertion-only patch.

    The agent emits (anchor_line, insertion_lines) and the runner composes the diff.
    This eliminates the diff-fidelity failures that plague LLM-generated patches —
    line numbers and context are computed from the real file content, not guessed.

    Contract:
      - anchor_line must appear EXACTLY ONCE in target_file (character-for-character
        match on a full line, excluding trailing newline). If 0 or >1 matches, we
        return (False, "", error).
      - insertion_lines are inserted immediately after the matched anchor line.
      - The diff uses `context` lines of surrounding context (default 3, git's default).

    Returns (ok, unified_diff, error_message).
    """
    path = wt_dir / target_file
    if not path.exists():
        return (False, "", f"target_file not found: {target_file}")

    raw = path.read_text()
    # Split preserving content; trailing newline becomes an empty trailing element.
    file_lines = raw.split("\n")
    # If file ends with newline, split gives trailing empty string — drop for indexing.
    had_trailing_nl = raw.endswith("\n")
    if had_trailing_nl:
        file_lines = file_lines[:-1]

    # Find the anchor (exact full-line match).
    matches = [i for i, line in enumerate(file_lines) if line == anchor_line]
    if len(matches) == 0:
        return (False, "", f"anchor_line not found in {target_file}")
    if len(matches) > 1:
        return (False, "", f"anchor_line appears {len(matches)} times in {target_file} (must be unique)")

    anchor_idx = matches[0]  # 0-indexed
    insert_after_line_number = anchor_idx + 1  # 1-indexed line number of anchor

    # Compose the hunk.
    ctx_before_start = max(0, anchor_idx - context + 1)  # include anchor as last context line
    ctx_before = file_lines[ctx_before_start : anchor_idx + 1]

    ctx_after_start = anchor_idx + 1
    ctx_after_end = min(len(file_lines), ctx_after_start + context)
    ctx_after = file_lines[ctx_after_start:ctx_after_end]

    # Hunk header: `@@ -old_start,old_count +new_start,new_count @@`
    # old_start is 1-indexed start of the context block on the left side.
    old_start = ctx_before_start + 1  # 1-indexed
    old_count = len(ctx_before) + len(ctx_after)
    new_start = old_start
    new_count = old_count + len(insertion_lines)

    diff_lines: list[str] = []
    diff_lines.append(f"--- a/{target_file}")
    diff_lines.append(f"+++ b/{target_file}")
    diff_lines.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
    for cl in ctx_before:
        diff_lines.append(" " + cl)
    for il in insertion_lines:
        diff_lines.append("+" + il)
    for cl in ctx_after:
        diff_lines.append(" " + cl)

    diff_text = "\n".join(diff_lines) + "\n"
    return (True, diff_text, "")


def validate_onboard_worktree(wt_dir: Path) -> dict:
    """Lightweight syntax check for install-surface patches. Runs `bash -n` on
    changed .sh files and `node --check` on changed .js files. Skips .md files.
    Returns a dict compatible in shape with validate_worktree (syntax_ok,
    syntax_errors) but with import_ok always True (no Python surface)."""
    changed = git_diff_names(wt_dir)
    syntax_ok = True
    syntax_errors: list[str] = []

    for rel in changed:
        full = wt_dir / rel
        if not full.exists():
            continue
        if rel.endswith(".sh"):
            r = _run(["bash", "-n", str(full)], cwd=wt_dir, check=False)
            if r.returncode != 0:
                syntax_ok = False
                syntax_errors.append(f"{rel}: {r.stderr.strip()}")
        elif rel.endswith(".js"):
            r = _run(["node", "--check", str(full)], cwd=wt_dir, check=False)
            if r.returncode != 0:
                syntax_ok = False
                syntax_errors.append(f"{rel}: {r.stderr.strip()}")

    return {
        "syntax_ok": syntax_ok,
        "import_ok": True,
        "syntax_errors": syntax_errors,
        "import_error": None,
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
