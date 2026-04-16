# Context Rot — Seed Problem Definition

This is the starting definition the agent refines. It is NOT the final output — it's the ground truth the agent builds from.

## What context rot IS

Context rot is the progressive degradation of AI tool effectiveness as conversation history accumulates, ages, or fragments across sessions and surfaces. It is a structural failure of how AI tools manage memory, not a user error.

## Three manifestations

1. **Missing context**: The AI has no memory of prior decisions. "We decided to use PostgreSQL last Tuesday" — but this session started fresh, so the AI suggests MongoDB.

2. **Stale context**: The AI remembers something that's no longer true. The pricing sheet changed, the team structure shifted, the deployment target moved from AWS to Railway. The AI confidently acts on outdated information.

3. **Irrelevant context**: The AI retains too much, and old context drowns current intent. Uncle Bob's observation: "it remembers too much." The 1M token window fills with yesterday's debugging session, and today's architecture discussion gets contaminated.

## Why bigger windows don't help

- @dbreunig measured "SIGNIFICANT decrease in performance at tokens > 20% consumed on Opus 4.6"
- Chroma Research quantified that degradation is discontinuous, not gradual
- Spotify Engineering reported models "forgetting the original task after a few turns"
- More tokens ≠ better memory. More tokens = more noise, more staleness, more attention dispersion.

## Why managed memory doesn't help

- Anthropic memory_stores is a black box — you can't inspect what it remembers, debug why it forgot, or fix a wrong memory
- Per-seat licenses (ChatGPT, Copilot) silo memory by user — one person's context is invisible to the team
- File-based memory (CLAUDE.md) rots by accumulation — @alxfazio: "useless 6k line context rot"

## The economic shape

Context rot costs time (re-explanation), accuracy (acting on stale data), and trust (users stop relying on AI for important tasks). The cost is invisible because it's distributed across every AI interaction, not concentrated in a single failure event.
