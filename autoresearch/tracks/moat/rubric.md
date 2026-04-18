# Moat track rubric — three-axis scoring for bmfote positioning hypotheses

**FROZEN DOCUMENT.** Editing this file invalidates `state/best/moat.json` and restarts the moat track score history.

You are evaluating a **positioning hypothesis** for bmfote — a cross-agent memory system for AI tools. Three ground-truth posts are attached above this rubric; treat them as the source of truth for what bmfote believes and who it's for.

Your job: score the candidate's (why / how / what) triple on three independent axes, each 1–10 as an integer. Anchor your score to the tier exemplars below. Do not average anchor scores — pick the tier the candidate most resembles and use its number.

---

## Axis 1: Minimalism coherence (grounded in Post 1)

Does the pitch reinforce "fewer moving parts," or does it sneak in complexity? Post 1's thesis is that **every layer you remove makes the system better** and the moat is radical simplicity — SQLite instead of vector DB, FTS instead of RAG, hooks instead of a framework, single thread instead of orchestration.

### Tier 1 — anti-minimalism
The pitch implies a new layer, framework, platform, orchestration engine, agent runtime, or config surface. Uses architectural-sounding nouns. Would need a diagram to explain.

**Exemplars (score = 1–2):**
- "bmfote is an intelligent AI memory platform that orchestrates cross-agent context across your workflow."
- "A distributed memory fabric for enterprise AI deployments."
- "The unified retrieval-augmented context engine for agent systems."

### Tier 5 — neutral
Doesn't actively add complexity but doesn't lean into simplicity either. Could describe any of 20 tools. No stance on the minimalism argument at all.

**Exemplars (score = 4–6):**
- "bmfote gives your team a shared memory across Claude, Cursor, and ChatGPT."
- "Remember everything your AI tools learn, across sessions and devices."
- "Memory that follows your AI wherever it goes."

### Tier 10 — radically minimalist
Explicitly names what bmfote is NOT (vector DB, framework, agent runtime, RAG pipeline) or references the concrete primitives (SQLite, FTS, hooks, single file, one REST endpoint). Treats minimalism as the moat, not a feature. A competitor couldn't adopt this positioning without contradicting their own pitch.

**Exemplars (score = 9–10):**
- "bmfote is a SQLite file your team shares. No vector DB, no framework, no orchestration — just a file every AI tool can read and write."
- "Cross-agent memory in 9 REST endpoints and one SQLite file. If it breaks, you can open it in a text editor."
- "The AI memory layer without a framework. Hooks write, FTS reads, your team owns the file."

---

## Axis 2: Category ownership (grounded in Post 2)

Does the pitch stake "cloud context / experiential memory" as a category bmfote owns? Post 2's thesis is that nobody has championed this category yet — the name is available, and the first one to claim it and make it legible to non-experts wins.

### Tier 1 — cedes the category
Uses generic "AI memory" language that could describe Mem0, Zep, or Anthropic memory_stores equally well. Or worse, uses language from adjacent categories (RAG, knowledge base, vector search, enterprise search) that actively miscategorizes bmfote.

**Exemplars (score = 1–2):**
- "bmfote is a memory layer for AI agents."
- "A vector search engine for LLM context."
- "Knowledge base for AI workflows."
- "RAG infrastructure for your agents."

### Tier 5 — uses bmfote-specific language but doesn't claim the category
References "shared context" or "cross-agent memory" without explicitly naming or defending a category. A reader who hasn't heard of bmfote wouldn't know what kind of thing it is.

**Exemplars (score = 4–6):**
- "bmfote lets your agents remember what happened across sessions and tools."
- "Shared context for your AI stack."
- "The memory layer that connects your AI tools."

### Tier 10 — stakes the category explicitly
Explicitly names "cloud context" or "experiential memory" as the category bmfote owns. Makes the category legible to a reader who hasn't heard the term. Bonus for framing the category as underserved or uncontested.

**Exemplars (score = 9–10):**
- "bmfote is cloud context — drop-in experiential memory that works across every AI tool and every device. The category nobody's fighting for yet."
- "Experiential memory for agents: a shared brain your whole team writes to, accessible from any AI tool, any device, any time. Like Dropbox for AI context."
- "Cloud context is the missing layer between your AI tools. bmfote is the first to ship it as a single file your team owns."

---

## Axis 3: Persona grounding (grounded in Post 3)

Does the pitch name a concrete operator persona + a concrete broken workflow? Post 3's thesis is that the pain is universal ("AI is in a silo") but the buyer is specifically "a person who is suffering from the silo AND has authority to fix it" — bottom-up, not enterprise IT top-down.

### Tier 1 — abstract audience
Targets "AI engineers," "developers," "teams," "companies," "businesses." No named role, no named broken workflow. Could be any SaaS tool's pitch.

**Exemplars (score = 1–2):**
- "bmfote helps teams build better AI workflows."
- "For developers who want smarter AI assistance."
- "Empowering businesses with AI memory."
- "Built for modern AI-native companies."

### Tier 5 — named persona, gestured problem
Names a persona at medium specificity (e.g., "small teams using AI," "SMB operators," "Claude Code users") and gestures at a problem but doesn't name a concrete recurring workflow that's broken today.

**Exemplars (score = 4–6):**
- "bmfote gives SMB operators a shared memory for their AI tools so context stops getting lost."
- "For small teams running Claude and ChatGPT side by side without shared context."
- "For ops leads whose AI tools don't talk to each other."

### Tier 10 — job title + concrete broken workflow
Names a specific job title AND a specific recurring broken workflow that a skeptical reader could validate in 5 minutes with one phone call to a real person in that role. The pain is observable, not hypothetical.

**Exemplars (score = 9–10):**
- "For the sales ops lead at a 40-person distributor who re-explains last week's deal to Claude every Monday morning because nothing persists between sessions."
- "For the solo consultant juggling 6 client accounts who has to paste their entire pricing sheet into ChatGPT every time they switch contexts."
- "For the engineering team lead whose junior devs use Copilot, senior devs use Claude Code, and nobody can answer 'what did we decide about the auth rewrite last sprint?'"
- "For the marketing manager at a 15-person agency who opens a new ChatGPT session for each client and loses the brand voice every time."

---

## Counter-positioning requirement

Every candidate MUST name a `counter_target` (specific competitor) and a `contradiction` (why bmfote wins against that competitor on a claim the competitor can't walk back).

Valid counter-targets include: Anthropic memory_stores, Mem0, Zep, LangGraph memory, "just use Claude's context window," Copilot-per-seat, Glean, Notion AI, "per-user ChatGPT licenses."

**Validate the contradiction by asking:**
1. Is the contradiction REAL and specific (not invented, not a generic "we're better")?
2. Would a skeptical version of that competitor concede it without walking back a public claim or their core value prop?

**Scoring adjustments for counter-positioning failures:**
- Missing counter_target entirely → `counter_target_valid = false` and minimalism axis capped at 3.
- counter_target named but contradiction is invented/false → `counter_target_valid = false` and both minimalism and category axes capped at 4.
- Generic "we're better" with no specifics → `counter_target_valid = false` and all three axes capped at 5.
- Valid, specific, defensible contradiction → `counter_target_valid = true`, no cap applied.

**Good contradiction example:**
- counter_target: "Anthropic memory_stores"
- contradiction: "managed black box vs. SQLite file you own, can back up with `cp`, grep, and fix when it breaks. Anthropic cannot concede this without walking back their managed-service positioning."

**Bad contradiction example (invented):**
- counter_target: "Mem0"
- contradiction: "Mem0 doesn't support real-time sync" ← unverified / likely false
- → `counter_target_valid = false`

---

## Anti-pattern word list — penalize on ALL axes

Any pitch that uses 3 or more of these words should not score above 5 on any axis. These are hedge/marketing words that indicate the pitch is generic:

`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`.

Report any flagged words found in the `anti_pattern_words` output field.

---

## Output format

You MUST return exactly one JSON object matching this schema. No prose before or after. No markdown fences. No comments.

```json
{
  "minimalism": <integer 1-10>,
  "minimalism_reason": "<one concise sentence>",
  "category": <integer 1-10>,
  "category_reason": "<one concise sentence>",
  "persona": <integer 1-10>,
  "persona_reason": "<one concise sentence>",
  "counter_target_valid": <true or false>,
  "counter_target_reason": "<one concise sentence>",
  "anti_pattern_words": ["<flagged words found in why/how/what, or empty array>"]
}
```

Scoring is integer 1–10 only. Do not return floats, ranges, or null. If you are unsure between two tiers, pick the lower one — the moat track is designed to be conservative.
