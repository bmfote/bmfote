# cctx positioning — validated via autoresearch (Nights 1 + 4, 160 experiments)

Night 1 (moat track): 80/80 promoted, 38 scored perfect 10/10/10.
Night 4 (context-rot track): 56/80 promoted, avg score 8.58.

This document captures the validated findings. Use it as the reference when
writing about cctx — blog posts, pitch decks, outbound, landing pages.

---

## The Problem: Context Rot

Context rot is when AI tools give worse answers the more you use them — stale
data contaminates outputs, context from unrelated tasks bleeds across sessions,
and users spend more time re-explaining what the AI should already know than
getting actual work done.

### Three failure modes

1. **Context archaeology** — Users craft defensive prompts to route around stale
   context. "Ignore last month's pricing, don't use the old messaging framework,
   forget the previous project." The clarification takes longer than the task.
   Cost: **$125K/year** for a 25-person team (3 min × 8 requests/day × 250 days).

2. **Stale data in customer-facing work** — AI-generated proposals cite deprecated
   features, sunset integrations, or superseded pricing. An enterprise prospect
   tests a claim, discovers it's wrong, and a $180K deal stalls for 3 weeks.
   Cost: **$81K/year** in rework + damaged deals.

3. **Cross-contamination** — AI mixes context from unrelated customers, projects,
   or accounts. Customer A's API keys appear in Customer B's troubleshooting
   response. Not a feature gap — a data leak waiting to happen.
   Cost: **$26K/year** per 5-person support team + unmeasured compliance risk.

### Why the obvious fixes fail

- **Bigger context windows** make it worse. @dbreunig measured "SIGNIFICANT
  decrease in performance at tokens > 20% consumed on Opus 4.6." More tokens =
  more stale data drowning current context.
- **Managed memory (memory_stores)** is a black box. You can't inspect what it
  remembers, debug why it forgot, or fix a wrong memory.
- **File-based memory (CLAUDE.md)** rots by accumulation. @alxfazio: "it's just
  updating the claude.md until it turns into a useless 6k line context rot."
- **Per-seat licenses** silo memory by user. One person's context is invisible
  to the team.

### What context rot costs

| Angle | Annual cost (25-person team) |
|-------|---:|
| Context archaeology (re-explanation overhead) | $125,000 |
| Stale data in outputs (rework + deal damage) | $81,000 |
| Team context silos (handoff rebuilds) | $31,000 |
| **Total** | **$237,000** |

### Evidence

- Chroma Research: quantified unpredictable performance degradation at context thresholds
- @dbreunig: "1M context doesn't matter" past 20% utilization
- @unclebobmartin: "One of the problems with a big context window is that it remembers too much"
- Spotify Engineering: "it tended to get lost when it filled up its context window"
- Atlan: defines rot as "missing, stale, conflicting, or irrelevant context"

See `docs/context-rot.md` for the full reference and `autoresearch/tracks/context-rot/target.jsonl`
for all 56 promoted experiments.

---

## The Solution: Cloud Context

## Category

**Cloud context.** Not "AI memory," not "context layer," not "memory platform."

cctx is cloud context — drop-in experiential memory across every AI tool and
every device. Like Dropbox moved files to the cloud, cctx moves AI context to
the cloud.

## Three irreducible claims

These three appeared in 100% of perfect-score (10/10/10) experiments:

1. **A SQLite file you own**
2. **Hooks auto-capture**
3. **FTS retrieves in <100ms**

## The Dropbox analogy

"Like Dropbox moved files to the cloud, cctx moves AI context to the cloud."

Present in 100% of perfect-score experiments.

## Minimalism as moat

"No vector DB, no framework, no orchestration" — the positioning, not a feature.
Every layer you remove makes the system better. Competitors who have already
committed to complexity cannot adopt this positioning without contradicting
their own pitch.

## ICP: SMB operators (20-40 person companies)

Distributors, contractors, brokerages, agencies. 45/80 promoted experiments
targeted the SMB channel. The universal pain: re-pasting business-specific
context (pricing tiers, customer preferences, equipment schedules) into every
new AI session, every day, 4-5x/day.

The buyer has authority to install the fix. Bottom-up, not enterprise IT.

## Nine validated counter-positions

| Counter-target | Core contradiction | Experiments |
|---|---|---:|
| Anthropic memory_stores | Managed black box vs. SQLite file you own | 15 |
| Mem0 | Vector-backed framework vs. SQLite FTS | 11 |
| Notion AI | Manual documentation vs. auto-capture | 11 |
| "just use Claude's context window" | Ephemeral + tool-locked | 11 |
| per-user ChatGPT licenses | Memory siloed by account | 10 |
| Copilot-per-seat | Context isolated to individual licenses | 6 |
| Zep | Requires running their service vs. one file | 6 |
| Glean | Requires IT to buy/configure vs. bottom-up install | 6 |
| LangGraph memory | Requires agents/graphs/state vs. 3 hooks + 9 endpoints | 4 |

Strongest counter-targets by average score: Copilot-per-seat (9.94),
LangGraph memory (9.84), Glean (9.83), Mem0 (9.82).

## Anti-pattern words (do not use)

`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`,
`cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`,
`engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`,
`streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`,
`end-to-end`, `scalable`.

Good positioning uses specific nouns (SQLite, FTS, distributor, Monday,
40-person) and specific verbs (grep, back up, share, paste, re-explain).

## Source data

See `autoresearch/tracks/moat/target.jsonl` for all 80 promoted experiments
with full scoring breakdowns.
