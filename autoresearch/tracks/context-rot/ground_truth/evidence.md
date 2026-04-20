# Context Rot — Practitioner Evidence

Compiled from concept graph research (April 2026). These are real quotes and findings from practitioners independently discovering context rot. Use them as evidence anchors — every proposal must cite at least one.

## Research

### Chroma Research Paper — "Context Rot"
Quantified that model performance varies significantly and unpredictably as input length changes. The degradation is not gradual — it's discontinuous. At certain thresholds, model accuracy drops sharply. This is the empirical anchor for the term "context rot."

### Atlan — "What Is Context Engineering?"
Defines context rot as failure from "missing, stale, conflicting, or irrelevant context." Frames it as the core constraint of context engineering. Names four failure modes explicitly.

## Practitioner Observations

### @dbreunig (industry practitioner)
"I notice SIGNIFICANT decrease in performance at tokens > 20% consumed on Opus 4.6. It degrades INSANELY, like the 1M context doesn't matter."

**Why it matters:** Directly refutes "just use a bigger context window." A 1M token window degrading at 200K tokens means 80% of the window is actively harmful.

### @unclebobmartin (Uncle Bob, software engineering thought leader)
"One of the problems with a big context window is that it remembers too much."

**Why it matters:** Reframes rot as not just forgetting but *remembering wrong things*. Retention without curation is a failure mode, not a feature.

### @shao__meng (AI practitioner)
Documents Claude Code's 1M window causing attention dispersion — "older irrelevant content starts to distract from current task."

**Why it matters:** Even the best-in-class context window (Claude's 1M) exhibits rot. This isn't a budget problem — it's architectural.

### Spotify Engineering (enterprise validation)
"It tended to get lost when it filled up its context window, forgetting the original task after a few turns."

**Why it matters:** Enterprise-grade validation that context rot affects production systems, not just hobby projects. Spotify's engineering team hit this ceiling.

### @alxfazio (developer practitioner)
"It's just updating the claude.md until it turns into a useless 6k line context rot."

**Why it matters:** Names the failure mode in file-based memory systems specifically. CLAUDE.md files — the most common persistence mechanism — rot by accumulation.

## Key Insight

Context rot is independently discovered, not marketed. Five unrelated practitioners named the same problem without coordinating. This is a category waiting to be claimed, not a term that needs to be invented.
