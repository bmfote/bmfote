# Code track rubric — four-axis scoring for cctx engine improvements

**FROZEN DOCUMENT.** Editing this file invalidates `state/best/code.json` and restarts the code track score history.

You are evaluating a **proposed code improvement** for cctx — a cross-agent memory system built on SQLite FTS5, hooks, and 9 REST endpoints. Four ground-truth posts and a reference audit are attached above this rubric; treat them as the source of truth for what cctx believes and how it should be built.

Your job: score the candidate's proposed change on four independent axes, each 1–10 as an integer. Anchor your score to the tier exemplars below. Do not average anchor scores — pick the tier the candidate most resembles and use its number.

---

## Axis 1: Correctness (does the diff fix the stated issue?)

### Tier 1 — broken or wrong
The diff does not parse as valid unified diff, introduces a syntax error, changes the wrong function, or "fixes" something that was not broken. Applying it would break the codebase.

**Exemplars (score = 1–2):**
- A diff that rewrites `query_search` but claims to fix the N+1 in `query_similar_error`.
- A diff with malformed `@@` hunk headers that `git apply` would reject.
- A diff that removes a working feature to "simplify" it.

### Tier 5 — partial fix
The diff applies cleanly and addresses the stated issue, but the fix is incomplete (handles the default case but not edge cases), or introduces a subtle behavioral change the description does not acknowledge.

**Exemplars (score = 4–6):**
- Replaces the N+1 loop with a JOIN but drops the content truncation (`[:800]`) that was in the original.
- Adds `hmac.compare_digest` for token comparison but uses the wrong import path.
- Adds composite indexes but misspells a column name.

### Tier 10 — merge-ready
The diff applies cleanly, fixes the exact stated issue, preserves all existing behavior, and handles edge cases. A reviewer would merge it as-is with no questions.

**Exemplars (score = 9–10):**
- Replaces the N+1 loop with a single LEFT JOIN, preserves truncation, preserves column order in the returned dict, adds no new imports beyond what is already available.
- Replaces `allow_origins=["*"]` with `allow_origins=os.getenv("CORS_ORIGINS", "").split(",")`, falling back to `["*"]` only in local dev mode (already has `is_remote_db()` to detect).

---

## Axis 2: Minimalism (does the fix remove complexity?)

Grounded in Post 1 ("every layer you remove makes the system better") and Post 4 ("one row per message, full text search on top"). Does the fix make the codebase simpler, or does it add moving parts?

### Tier 1 — adds complexity
The fix adds a new dependency, a new file, a new abstraction layer, a new configuration surface, or a substantial net positive line count. Would need a diagram to explain.

**Exemplars (score = 1–2):**
- "Fix the N+1 by adding a `QueryBatcher` class in `engine/query_utils.py`."
- "Add SQLAlchemy ORM layer to replace raw SQL queries."
- "Fix thread safety by adding Redis-based connection pooling."

### Tier 5 — neutral
The fix is about the same complexity as what it replaces — similar line count, no new abstractions, but also does not actively simplify. The codebase is not harder to understand, but not easier either.

**Exemplars (score = 4–6):**
- Replaces the N+1 loop with a different loop that uses `executemany`. Same structure, same line count.
- Adds type hints without removing any code — net positive lines, but strictly additive.

### Tier 10 — actively simplifies
The fix removes moving parts. Net negative lines. Removes an abstraction, a branch, a redundant path, or dead code. After the fix, the code is simpler to read and has less surface area for breakage.

**Exemplars (score = 9–10):**
- Replaces 18 lines (loop + inner query + dict assembly) with 12 lines (single JOIN + list comprehension). Net -6 lines, one query instead of N+1.
- Removes the `RAILWAY_ENVIRONMENT` fallback: 3 lines deleted, zero added, the code path was dead.
- Deletes `sync_conversations.py`'s duplicate `get_conn()` and imports from `engine.db` instead: net -4 lines.

---

## Axis 3: Reliability (does the fix make the system more robust?)

Grounded in Post 4 ("every correction, every preference, everything I told Claude to never do again, all sitting in one file the agent can search in under a second"). Does the fix protect the database, close a failure mode, or prevent data corruption?

### Tier 1 — weakens reliability
The fix introduces a new failure mode, weakens error handling, removes a safety check, or makes the code less thread-safe than before.

**Exemplars (score = 1–2):**
- "Fix CORS by removing the CORS middleware entirely."
- Removes the `SELECT 1` health check from `get_conn()`.
- Changes `conn.commit()` to `conn.execute("COMMIT")` without understanding libsql's autocommit mode.

### Tier 5 — neutral
The fix does not introduce new failure modes and does not close existing ones. A cleanup, style change, or documentation improvement with no runtime reliability impact.

**Exemplars (score = 4–6):**
- Adding return type hints. Correct and useful, but does not change runtime behavior.
- Renaming a variable for clarity. Zero reliability impact.

### Tier 10 — closes a failure mode
The fix directly prevents a security exposure, eliminates a data corruption path, or makes the system measurably more robust under concurrent load.

**Exemplars (score = 9–10):**
- Adds `hmac.compare_digest` for timing-safe token comparison — closes a side-channel attack vector.
- Adds `threading.Lock` around `_conn` in `get_conn()` — prevents race conditions under concurrent FastAPI requests.
- Replaces `allow_origins=["*"]` with an env-var-driven allowlist for cloud deployments.
- Adds composite indexes that eliminate full-table scans on the two hottest query paths.

---

## Axis 4: Taste (would a senior engineer merge this with no notes?)

Does the diff look like it was written by someone who knows this codebase? Does it match existing patterns, naming conventions, and import style?

### Tier 1 — careless
The diff has formatting inconsistencies, changes unrelated code, uses patterns inconsistent with the rest of the file, includes commented-out code or TODO markers, or the description is vague marketing language.

**Exemplars (score = 1–2):**
- A fix that uses `async def` in a file that is entirely sync.
- A fix that introduces `typing.TypedDict` when the file uses plain dicts everywhere.
- Description says "Optimize database queries for better performance" instead of naming the specific fix.

### Tier 5 — workable
The diff is clean and follows existing patterns, but the description is vague, or the change is so small it is debatable whether it is worth a separate patch. No errors, but no polish.

**Exemplars (score = 4–6):**
- Fixing a single bare `except` clause. Correct but low-impact.
- Adding a type hint to one function out of four — inconsistent scope.

### Tier 10 — ship it
The diff reads like it was written by someone who knows this codebase. Matches existing import order, naming conventions, docstring style. The description is precise — names the function, the problem, and the fix in one sentence. A reviewer would say "ship it" with no comments.

**Exemplars (score = 9–10):**
- Uses the same `rows_to_dicts` / `row_to_dict` helpers already in `db.py`.
- Uses the same `Optional[str]` style as the rest of the file.
- Description: "Collapse N+1 in query_similar_error to a single LEFT JOIN" — specific, verifiable, no filler.

---

## Anti-pattern word list — penalize on taste axis

Any description or rationale that uses 3 or more of these words should not score above 4 on the taste axis. These are hedge/marketing words that indicate the proposal is generic:

`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`.

Report any flagged words found in the `anti_pattern_words` output field.

---

## Output format

You MUST return exactly one JSON object matching this schema. No prose before or after. No markdown fences. No comments.

```json
{
  "correctness": <integer 1-10>,
  "correctness_reason": "<one concise sentence>",
  "minimalism": <integer 1-10>,
  "minimalism_reason": "<one concise sentence>",
  "reliability": <integer 1-10>,
  "reliability_reason": "<one concise sentence>",
  "taste": <integer 1-10>,
  "taste_reason": "<one concise sentence>",
  "anti_pattern_words": ["<flagged words found, or empty array>"]
}
```

Scoring is integer 1–10 only. Do not return floats, ranges, or null. If you are unsure between two tiers, pick the lower one — the code track is designed to be conservative.
