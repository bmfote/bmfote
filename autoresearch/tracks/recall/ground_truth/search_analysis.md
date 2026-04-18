# cctx Search Pipeline — Current State Analysis

## Architecture

The search pipeline is minimal by design: user query → `_auto_phrase()` → FTS5 MATCH → BM25 ranking → snippet extraction → results.

All search logic lives in `engine/server.py` (two functions) and `engine/schema.sql` (FTS5 virtual table definition).

## Current Implementation

### Query Pre-processing: `_auto_phrase(q)`

```python
def _auto_phrase(q: str) -> str:
    if not q or any(c in q for c in '"*():^'):
        return q  # Pass through explicit FTS5 syntax
    tokens = q.split()
    if any(op in tokens for op in ("AND", "OR", "NOT", "NEAR")):
        return q  # Pass through boolean operators
    return f'"{q}"'  # Wrap bare queries in quotes
```

This is the **only** query transformation. It wraps bare multi-token queries in quotes, forcing exact phrase matching. Single-token queries and queries with explicit FTS5 syntax pass through unchanged.

### Search SQL: `query_search()`

```sql
SELECT m.uuid, m.session_id, m.type, m.role, m.timestamp, m.model,
       s.project,
       snippet(messages_fts, 0, '>>>', '<<<', '...', 40) as snippet,
       bm25(messages_fts) as rank
FROM messages_fts f
JOIN messages m ON f.rowid = m.id
LEFT JOIN sessions s ON m.session_id = s.session_id
WHERE messages_fts MATCH ? AND m.workspace_id = ?
ORDER BY rank LIMIT ?
```

Standard FTS5 query with default BM25 ranking. No re-ranking, no boosting, no result diversification.

### FTS5 Schema

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id
);
```

- Single indexed column: `content`
- Default tokenizer: `unicode61`
- Default BM25 parameters: k₁=1.2, b=0.75
- No porter stemmer, no custom tokenizer options

## Known Limitations

### L1: Phrase wrapping kills multi-word topic queries (CRITICAL)
`_auto_phrase("autoresearch projects ranked")` → `'"autoresearch projects ranked"'` → FTS5 looks for that exact 3-word sequence, which doesn't exist. The relevant message says "Track Candidates" not "projects ranked". This is the #1 failure mode — any multi-word query that isn't an exact phrase returns zero results.

**Impact:** 19 of 30 eval queries return zero results today.

### L2: No stemming
The default `unicode61` tokenizer does not stem. "running" ≠ "run", "deployed" ≠ "deploy", "improvements" ≠ "improve". This causes misses on morphological variants.

### L3: No synonym or concept expansion
"bug fix" doesn't match "patch", "competitor" doesn't match "counter_target", "database connection problem" doesn't match "thread-safe connection singleton". Users think in natural language; the database stores technical terms.

### L4: No fallback strategy
When phrase match returns zero results, there's no fallback to bag-of-words, OR-expansion, or partial matching. The user gets nothing, not "here are some partial matches."

### L5: No prefix expansion for multi-word queries
Only explicit `term*` syntax triggers prefix matching. "deploy" doesn't match "deployment" or "deploying" unless the user manually adds `*`.

### L6: BM25 defaults may not suit conversation data
k₁=1.2 and b=0.75 are general-purpose defaults tuned for document collections. Conversation messages are shorter and more varied — different parameters might rank better.

### L7: snippet() token limit is hardcoded
40 tokens per snippet may truncate important context for long messages. Not configurable per query.

### L8: No recency signal in ranking
BM25 ranks purely on term frequency. Recent messages about "railway deploy" rank the same as months-old ones, even when the user clearly wants recent context.

## Improvement Priorities (ranked by impact on eval set)

1. **Fix L1 first** — switch from pure phrase to OR-expansion or mixed strategy when phrase returns no results. This alone would fix the majority of failing queries.
2. **Add stemming** (L2) — porter stemmer in FTS5 tokenizer config or query-time stem expansion.
3. **Add fallback** (L4) — if phrase match returns < N results, retry with bag-of-words.
4. **Consider prefix expansion** (L5) — add trailing `*` to tokens.
5. **BM25 tuning** (L6) and **recency boosting** (L8) are polish — address after L1-L4.

## Constraints

- Changes must be backward-compatible with explicit FTS5 syntax (users can pass `"exact phrase"`, `term*`, `AND`/`OR`/`NOT`, `NEAR`)
- No new Python dependencies
- No new database tables or endpoints
- Only modify files in `engine/`
- Prefer changes to `_auto_phrase()` and `query_search()` in `engine/server.py`
- Schema changes (`engine/schema.sql`) are valid but cannot be tested against the live index
