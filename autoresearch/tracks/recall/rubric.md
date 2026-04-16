# Recall Track — Judge Rubric

Score each proposed search improvement on four axes. Integer scores only (1–10). Lower tier if unsure.

## Axis 1: Retrieval (weight 0.40)

Does the change measurably improve search quality? Use the EVAL METRICS provided (MRR@10 delta, precision@5 delta, recall@5 delta, regression count).

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1–2 | Negative MRR delta (search got worse), OR eval unavailable and the change is logically unsound |
| Low | 3–4 | Zero or negligible MRR delta (<0.02), no meaningful improvement on any query category |
| Mid | 5–6 | Small positive MRR delta (0.02–0.08), improves 2-4 queries, no regressions |
| High | 7–8 | Moderate MRR delta (0.08–0.15), improves multiple query categories, zero regressions |
| Top | 9–10 | Large MRR delta (>0.15), improves majority of failing queries, zero regressions, precision and recall both improve |

**Regression penalty:** If `queries_regressed > 0`, cap retrieval score at 6 regardless of MRR improvement. A change that helps some queries but breaks others is net-negative.

**Schema change exception:** If `schema_change_detected` is true, eval metrics are unavailable. Score retrieval based on the logical soundness of the change (e.g., adding porter stemmer is well-established; adding random tokenizer options is not). Cap at 7 for schema-only changes without measured metrics.

## Axis 2: Minimalism (weight 0.25)

Does the change stay minimal? Fewer moving parts = fewer bugs = faster search.

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1–2 | Adds new dependency, new file, new abstraction layer, new config system |
| Low | 3–4 | Significant net positive lines (>20), adds new helper functions or classes |
| Mid | 5–6 | Modest addition (5–20 net lines), one new helper function, reasonable complexity |
| High | 7–8 | Small addition (<5 net lines), modifies existing function in-place, clean |
| Top | 9–10 | Net negative lines, removes complexity, simplifies existing logic while improving results |

## Axis 3: Reliability (weight 0.20)

Does the change maintain or improve reliability? Will it work on every query, not just the eval set?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1–2 | Breaks existing FTS5 syntax passthrough, crashes on edge cases, SQL injection risk |
| Low | 3–4 | Handles happy path only, no error handling for malformed queries, might fail on empty input |
| Mid | 5–6 | Handles common cases correctly, FTS5 passthrough preserved, basic edge cases covered |
| High | 7–8 | Robust against empty/whitespace/unicode input, FTS5 passthrough verified, no new failure modes |
| Top | 9–10 | Closes existing failure modes (e.g., catches FTS5 parse errors that currently crash), zero new risk |

## Axis 4: Taste (weight 0.15)

Does the code fit the codebase? Clean, idiomatic, well-described?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1–2 | Formatting inconsistencies, wrong naming conventions, commented-out code, vague description |
| Low | 3–4 | Functional but messy, unclear variable names, description doesn't match what the code does |
| Mid | 5–6 | Clean code, follows patterns, but description is generic or low-specificity |
| High | 7–8 | Matches existing style exactly (import order, quotes, docstring style), precise description |
| Top | 9–10 | Improves readability of surrounding code, description names exact function and mechanism |

## Anti-pattern words

Penalize taste axis by -1 if 3 or more found in description/rationale:
`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`

## Composite score

```
composite = 0.40 × retrieval + 0.25 × minimalism + 0.20 × reliability + 0.15 × taste
```

## Promotion gate

- `composite >= 7.5`
- `min(retrieval, minimalism, reliability, taste) >= 5`
- Both conditions must hold
