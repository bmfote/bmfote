# Context Rot — The Problem cctx Exists to Solve

Validated via autoresearch Night 4: 80 experiments, 56 promoted (70% rate),
avg composite 8.58. This document compiles the best problem definitions across
7 distinct angles, with evidence citations and quantified cost models.

Use this as the reference for content creation: blog posts, LinkedIn posts,
pitch deck problem slides, investor narrative.

---

## One-sentence definition

> Context rot is when users spend more time crafting defensive prompts to route
> around stale context than the AI saves on the task — turning every interaction
> into "context archaeology" where you explain what to ignore before you can
> explain what to do.

— Experiment #007, composite 9.70 (10/10/9)

---

## Seven angles

### 1. Context Archaeology — the invisible tax

**Definition:** Users craft defensive prompts to work around what the AI
wrongly remembers. "Ignore last month's persona definitions. Don't use the old
pricing tiers. Use the new messaging framework, not the one from three threads
ago." The clarification often takes longer than the task itself.

**Cost model:** 25 people × 3 min/request × 8 requests/day × 250 days =
2,500 hours/year at $50/hr = **$125,000/year** in prompt overhead.

**Why it matters:** Every AI user already pays this cost but nobody's quantified
it. This angle makes the invisible visible. A VP of Ops can put this number in
a budget review.

**Counter-narrative:** Prompt engineering can steer the AI in a single session,
but it can't fix structural memory fragmentation. No prompt can tell the AI what
to forget when stale information accumulates faster than users can invalidate it.

---

### 2. Stale Data Risk — the deal killer

**Definition:** AI tools accumulate product history across rapid iteration
cycles, causing customer-facing materials to cite deprecated features, sunset
integrations, or superseded capabilities as if they're current.

**Scenario:** A Sales Enablement Manager uses Claude to draft proposals. By
month 7, proposals randomly cite "real-time Salesforce sync" (deprecated to
batch-only in March), "free 10GB storage" (changed to 5GB in May), and "99.99%
SLA" (downgraded to 99.9% in April). An enterprise prospect's technical team
tests the real-time sync claim, discovers it's batch-only. $180K deal stalls.

**Cost model:** 720 proposals/year, 25% stale after 6 months: $12K internal
rework + $21K customer delays + $48K damaged deals = **$81,000/year**.

**Counter-narrative:** "Just maintain a source of truth doc" assumes the AI
prioritizes the latest doc over accumulated history. Uncle Bob: "One of the
problems with a big context window is that it remembers too much." Old feature
descriptions don't disappear when you add a new doc.

---

### 3. Cross-Contamination — the trust destroyer

**Definition:** AI tools can't retain company-specific knowledge across sessions,
forcing you to re-teach the same policies, bugs, and decisions every conversation
— or risk contaminating one task with details from another.

**Scenario:** A support engineer handles 12 tickets/day. By ticket #4, they're
either re-explaining the company's API rate limits every time (fresh sessions),
or Claude starts mixing Customer A's timeout issue into Customer B's
authentication ticket (long sessions).

**Cost model:** 12 tickets × 3 min re-explanation = 3 hrs/week per engineer.
5-person team at $35/hr = **$26,250/year** + unmeasured accuracy loss when stale
context sends a deprecated fix to a paying customer.

**Counter-narrative:** Spotify Engineering reported their AI "tended to get lost
when it filled up its context window, forgetting the original task after a few
turns." A support engineer keeping 12 tickets in one 1M-token session doesn't
get better responses — they get Customer A's API keys suggested for Customer B.

---

### 4. Knowledge Loss — institutional amnesia

**Definition:** AI tools get worse at their job as you use them more, because
accumulating conversation history degrades accuracy before the context window
is even 25% full.

**Scenario:** An Engineering Manager tracks architecture decisions across a
6-month migration. By month 3, the project context hits 220K tokens (22% of
the 1M window). Claude starts suggesting database patterns explicitly rejected
in month 1, recommending incompatible extensions ruled out in earlier sessions.

**Cost model:** 6 engineers × 3 times/week × 18 min re-explaining settled
decisions = 280 hrs/year at $95/hr = $26,600 + 4 rollback incidents at $5,320 =
**$31,920/year** for one project.

**Counter-narrative:** @dbreunig measured "SIGNIFICANT decrease in performance
at tokens > 20% consumed on Opus 4.6" — at 200K tokens, the remaining 800K
actively degrade accuracy. @shao__meng documented this as attention dispersion.
Bigger windows make the problem worse, not better.

---

### 5. Team Fragmentation — the silo multiplier

**Definition:** AI tools fragment memory across team members, making each
person's session blind to context owned by others — forcing redundant
re-explanation and causing cross-functional misalignment.

**Scenario:** An enterprise deal involves 4 roles (AE, SE, CSM, Solutions
Architect), each using Claude independently. The AI sessions are siloed per
person — when the SE asks about pricing from discovery, it has no context from
the AE's session. Complete context rebuild at every handoff.

**Cost model:** 40 deals/year, 70% have fragmentation incidents: $16K re-sync
overhead + $15K fixing customer-facing errors from misalignment =
**$31,220/year** for one sales team.

**Counter-narrative:** "Use Slack or shared docs to align context" assumes teams
will prospectively document what the AI needs. Spotify Engineering found models
"forgetting the original task after a few turns" — if the AI can't maintain
context within a single session, expecting humans to manually share context
across sessions adds overhead rather than removing it.

---

### 6. Threshold Collapse — the cliff edge

**Definition:** AI performance doesn't degrade gradually but collapses
unpredictably at hidden thresholds — fine at message 47, catastrophically wrong
at message 48, with no warning.

**Scenario:** A technical account manager uses Claude to prep for a renewal
call, reviewing 6 months of support tickets and billing history. At message 52,
Claude confidently states the client is on the Enterprise plan (they're on
Professional) and references a Q3 bug from a different customer. Every fact in
the thread is now suspect.

**Cost model:** 10-person CS team, 200 tickets/month, 15% hit unpredictable
collapse requiring 25-min recovery: 30 collapses × 25 min = 12.5 hrs/month =
**$9,000/year** at $60/hr.

**Counter-narrative:** @dbreunig measured Opus 4.6's 1M window degrading
"INSANELY" at 20% utilization — 800K tokens are unusable. Chroma Research proved
collapse is structural: larger windows move the threshold but don't eliminate it.

---

### 7. File-Based Memory Rot — the CLAUDE.md trap

**Definition:** File-based memory systems (CLAUDE.md, .cursorrules) rot by
accumulation — as past context piles up, the file becomes a 6K-line dump that
actively misleads the AI.

**Scenario:** A CSM managing 10 enterprise accounts needs to re-feed account
history (deployment details, past tickets, custom integrations) for every
escalation. When they forget to mention a migration from AWS to Azure, Claude
confidently recommends AWS-specific fixes, creating customer-visible errors.

**Cost model:** $3,450/CSM × 5-person team = **$17,250/year**.

**Counter-narrative:** @alxfazio documented CLAUDE.md files becoming "useless
6k line context rot." Atlan's research defines rot as "missing, stale,
conflicting, or irrelevant context" — file-based memory delivers all four.

---

## Evidence Wall

| Source | Quote | What it proves |
|--------|-------|----------------|
| **Chroma Research** | Quantified unpredictable performance degradation at context thresholds | Rot is structural, not gradual |
| **@dbreunig** | "SIGNIFICANT decrease in performance at tokens > 20% consumed on Opus 4.6. It degrades INSANELY, like the 1M context doesn't matter." | Bigger windows don't help |
| **@unclebobmartin** | "One of the problems with a big context window is that it remembers too much." | Retention without curation is a failure mode |
| **@shao__meng** | Claude Code 1M window: "older irrelevant content starts to distract from current task" | Even best-in-class windows rot |
| **Spotify Engineering** | "It tended to get lost when it filled up its context window, forgetting the original task after a few turns" | Enterprise-grade validation |
| **@alxfazio** | "It's just updating the claude.md until it turns into a useless 6k line context rot" | File-based memory rots too |
| **Atlan** | Defines rot as "missing, stale, conflicting, or irrelevant context" | Academic framing of the four failure modes |

---

## Stacked ROI

| Angle | Annual cost (25-person team) |
|-------|---:|
| Context archaeology | $125,000 |
| Stale data in outputs | $81,000 |
| Team context silos | $31,000 |
| Threshold collapse recovery | $9,000 |
| **Total** | **$246,000** |

For a CFO: "Context rot costs your company a quarter million annually. Most of
it is invisible because it's 3 minutes at a time, 8 times a day, across every
person who uses an AI tool."

---

## Source data

See `autoresearch/tracks/context-rot/target.jsonl` for all 56 promoted experiments
with full scoring breakdowns, and `autoresearch/tracks/context-rot/ground_truth/evidence.md`
for the source evidence.
