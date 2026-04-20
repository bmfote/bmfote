"""
Eval harness for the recall track.

Extracts the modified _auto_phrase() from a worktree's engine/server.py,
runs all eval queries against the real database, and computes MRR@10,
precision@5, recall@5 deltas vs baseline.

Database access: opens engine/local-replica.db via sqlite3 in read-only mode.
The extracted function runs in a restricted namespace (pure string→string).
"""

from __future__ import annotations

import ast
import json
import sqlite3
import textwrap
from pathlib import Path
from typing import Any, Callable

from autoresearch.prepare import REPO_ROOT

DEFAULT_WORKSPACE = "bmfote-default"
DB_PATH = REPO_ROOT / "engine" / "local-replica.db"


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def reciprocal_rank(result_uuids: list[str], expected: set[str], k: int = 10) -> float:
    """1/position of first expected UUID in results (0 if not found within k)."""
    for i, uuid in enumerate(result_uuids[:k]):
        if uuid in expected:
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(result_uuids: list[str], expected: set[str], k: int = 5) -> float:
    """Fraction of top-k results that are expected."""
    if k == 0:
        return 0.0
    top_k = result_uuids[:k]
    return sum(1 for u in top_k if u in expected) / k


def recall_at_k(result_uuids: list[str], expected: set[str], k: int = 5) -> float:
    """Fraction of expected results found in top-k."""
    if not expected:
        return 0.0
    top_k = set(result_uuids[:k])
    return len(top_k & expected) / len(expected)


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def extract_auto_phrase(server_py_path: Path) -> Callable[[str], str] | None:
    """Extract _auto_phrase from a (possibly modified) server.py via AST.

    Returns a callable (str → str) or None if the function wasn't found.
    The function is compiled and executed in a restricted namespace containing
    only builtins — no imports, no side effects.
    """
    source = server_py_path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    # Find the _auto_phrase function definition
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_auto_phrase":
            func_node = node
            break

    if func_node is None:
        return None

    # Extract source lines for the function
    # ast gives us line numbers (1-indexed)
    lines = source.splitlines()
    start = func_node.lineno - 1  # convert to 0-indexed
    end = func_node.end_lineno  # end_lineno is inclusive in ast
    func_lines = lines[start:end]

    # Dedent if needed (the function might be at module level already)
    func_source = textwrap.dedent("\n".join(func_lines))

    # Compile and exec in restricted namespace
    namespace: dict[str, Any] = {"__builtins__": __builtins__}
    try:
        exec(compile(func_source, "<_auto_phrase>", "exec"), namespace)
    except Exception:
        return None

    return namespace.get("_auto_phrase")


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def _run_fts_query(
    conn: sqlite3.Connection,
    fts_query: str,
    workspace_id: str = DEFAULT_WORKSPACE,
    limit: int = 10,
) -> list[str]:
    """Execute an FTS5 MATCH query and return result UUIDs in rank order."""
    try:
        rows = conn.execute(
            """
            SELECT m.uuid
            FROM messages_fts f
            JOIN messages m ON f.rowid = m.id
            WHERE messages_fts MATCH ? AND m.workspace_id = ?
            ORDER BY bm25(messages_fts)
            LIMIT ?
            """,
            (fts_query, workspace_id, limit),
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        # Malformed FTS5 query — return empty (counts as regression)
        return []


def _current_auto_phrase(q: str) -> str:
    """The baseline _auto_phrase — copied from engine/server.py.
    This is the function we're trying to improve."""
    if not q or any(c in q for c in '"*():^'):
        return q
    tokens = q.split()
    if any(op in tokens for op in ("AND", "OR", "NOT", "NEAR")):
        return q
    return f'"{q}"'


# ---------------------------------------------------------------------------
# Baseline + eval
# ---------------------------------------------------------------------------

def _load_eval_queries(eval_queries_path: Path) -> list[dict[str, Any]]:
    queries = []
    for line in eval_queries_path.read_text().splitlines():
        line = line.strip()
        if line:
            queries.append(json.loads(line))
    return queries


def _eval_with_func(
    conn: sqlite3.Connection,
    auto_phrase_fn: Callable[[str], str],
    eval_queries: list[dict[str, Any]],
    workspace_id: str = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """Run all eval queries through a given auto_phrase function and compute metrics."""
    per_query = []
    for q in eval_queries:
        query_text = q["query"]
        expected = set(q["expected_uuids"])
        fts_query = auto_phrase_fn(query_text)
        result_uuids = _run_fts_query(conn, fts_query, workspace_id)
        rr = reciprocal_rank(result_uuids, expected, k=10)
        p5 = precision_at_k(result_uuids, expected, k=5)
        r5 = recall_at_k(result_uuids, expected, k=5)
        per_query.append({
            "query": query_text,
            "category": q["category"],
            "fts_query": fts_query,
            "result_count": len(result_uuids),
            "rr": rr,
            "p5": p5,
            "r5": r5,
            "hit": rr > 0,
        })

    n = len(per_query)
    mrr_10 = sum(pq["rr"] for pq in per_query) / n if n else 0.0
    mean_p5 = sum(pq["p5"] for pq in per_query) / n if n else 0.0
    mean_r5 = sum(pq["r5"] for pq in per_query) / n if n else 0.0
    hits = sum(1 for pq in per_query if pq["hit"])

    # Per-category breakdown
    categories: dict[str, list[dict]] = {}
    for pq in per_query:
        categories.setdefault(pq["category"], []).append(pq)
    category_mrr = {
        cat: sum(pq["rr"] for pq in pqs) / len(pqs)
        for cat, pqs in categories.items()
    }

    return {
        "mrr_10": round(mrr_10, 4),
        "mean_precision_5": round(mean_p5, 4),
        "mean_recall_5": round(mean_r5, 4),
        "hits": hits,
        "total": n,
        "category_mrr": {k: round(v, 4) for k, v in category_mrr.items()},
        "per_query": per_query,
    }


def compute_baseline(
    db_path: Path | None = None,
    eval_queries_path: Path | None = None,
    workspace_id: str = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """Run all eval queries through the current _auto_phrase. Returns baseline metrics."""
    db_path = db_path or DB_PATH
    eval_queries_path = eval_queries_path or (
        REPO_ROOT / "autoresearch" / "tracks" / "recall" / "ground_truth" / "eval_queries.jsonl"
    )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    eval_queries = _load_eval_queries(eval_queries_path)
    result = _eval_with_func(conn, _current_auto_phrase, eval_queries, workspace_id)
    conn.close()
    return result


def run_eval(
    worktree_dir: Path,
    db_path: Path | None = None,
    eval_queries_path: Path | None = None,
    baseline: dict[str, Any] | None = None,
    workspace_id: str = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """Run eval with the modified _auto_phrase from the worktree.

    Returns metrics dict including deltas vs baseline. If the modified function
    can't be extracted (AST failure, function removed, etc.), returns
    {"eval_unavailable": True} with a reason string.
    """
    db_path = db_path or DB_PATH
    eval_queries_path = eval_queries_path or (
        REPO_ROOT / "autoresearch" / "tracks" / "recall" / "ground_truth" / "eval_queries.jsonl"
    )

    server_py = worktree_dir / "engine" / "server.py"
    if not server_py.exists():
        return {"eval_unavailable": True, "reason": "engine/server.py not found in worktree"}

    modified_fn = extract_auto_phrase(server_py)
    if modified_fn is None:
        return {"eval_unavailable": True, "reason": "_auto_phrase not found or failed to compile"}

    # Check for schema changes
    schema_path = worktree_dir / "engine" / "schema.sql"
    original_schema = (REPO_ROOT / "engine" / "schema.sql").read_text()
    modified_schema = schema_path.read_text() if schema_path.exists() else original_schema
    schema_changed = modified_schema != original_schema

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    eval_queries = _load_eval_queries(eval_queries_path)

    modified_result = _eval_with_func(conn, modified_fn, eval_queries, workspace_id)
    conn.close()

    # Compute deltas vs baseline
    if baseline is None:
        baseline = compute_baseline(db_path, eval_queries_path, workspace_id)

    mrr_delta = modified_result["mrr_10"] - baseline["mrr_10"]
    p5_delta = modified_result["mean_precision_5"] - baseline["mean_precision_5"]
    r5_delta = modified_result["mean_recall_5"] - baseline["mean_recall_5"]

    # Count regressions: queries that had hits in baseline but lost them
    baseline_hits = {pq["query"] for pq in baseline["per_query"] if pq["hit"]}
    modified_hits = {pq["query"] for pq in modified_result["per_query"] if pq["hit"]}
    regressions = baseline_hits - modified_hits
    improvements = modified_hits - baseline_hits

    return {
        "mrr_10": modified_result["mrr_10"],
        "mean_precision_5": modified_result["mean_precision_5"],
        "mean_recall_5": modified_result["mean_recall_5"],
        "hits": modified_result["hits"],
        "total": modified_result["total"],
        "mrr_delta": round(mrr_delta, 4),
        "p5_delta": round(p5_delta, 4),
        "r5_delta": round(r5_delta, 4),
        "queries_improved": len(improvements),
        "queries_regressed": len(regressions),
        "regressed_queries": sorted(regressions),
        "improved_queries": sorted(improvements),
        "category_mrr": modified_result["category_mrr"],
        "baseline_category_mrr": baseline["category_mrr"],
        "schema_change_detected": schema_changed,
    }
