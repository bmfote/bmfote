# Distribution track rubric — three-axis scoring for cctx launch plans

**FROZEN DOCUMENT.** Editing this file invalidates `state/best/distribution.json` and restarts the distribution track score history.

You are evaluating a **launch plan** for cctx — a cloud context system for AI agents. Three positioning posts and a constraints document are attached above this rubric. The constraints file contains hard exclusions (no paid ads, $5/mo Anthropic cap, solo operator) and a comparables list. Treat them as the source of truth.

Score the candidate on three independent axes, each 1–10 as an integer. Anchor your score to the tier exemplars below. Do not average — pick the tier the plan most resembles and use its number.

---

## Axis 1: Feasibility (grounded in constraints.md)

Can a solo operator with $0 ad budget and $0–50/mo infra actually ship this plan in the next 30 days without hiring anyone or raising money? Does it respect the $5/mo Anthropic API cap? Does it avoid any excluded tactic (paid ads, cold outbound at scale, enterprise sales motion, content calendar)?

### Tier 1 — infeasible given constraints
Requires ad budget, enterprise sales motion, BDR hire, $500+/mo infra, a marketing team, or assumes new product features that don't exist today. Or ignores the $5/mo Anthropic cap (e.g., "free hosted demo where every user gets unlimited Claude queries").

**Exemplars (score = 1–3):**
- "Launch a $500/mo LinkedIn Ads campaign targeting CTOs."
- "Hire a growth engineer to run a content calendar with 3 SEO-optimized posts per week."
- "Free unlimited hosted demo — users get 100 Claude queries per session."
- "Build a waitlist-gated web dashboard first, then launch."
- "Send 200 cold emails per day to engineering leaders."

### Tier 5 — partially feasible
Executable in theory but assumes the operator has more bandwidth or infra than the constraints allow. May require 1–2 things that technically work but strain the budget or time.

**Exemplars (score = 4–6):**
- "Run a Show HN launch plus maintain a weekly blog."
- "Host a free demo capped at 10 Claude queries per workspace per day."
- "Launch on Product Hunt and respond to every comment within an hour."

### Tier 10 — executable this week
One operator can ship this tactic in 1–7 days with existing infra. Cost: $0–50/mo. Respects every constraint, including the Anthropic cap. The demo mechanism gracefully degrades (static demo, recorded walkthrough, rate-limited sandbox, or "bring your own key"). No content-calendar dependency.

**Exemplars (score = 9–10):**
- "One-command `curl | bash` installer + a 90-second Loom demo + a single Show HN post Tuesday 9am EST. Infra: existing Railway free tier. Time cost: 2 days."
- "GitHub README as the demo — show the 4 MCP tools working in a terminal gif. Launch thread on ClaudeAI subreddit. No hosted demo; users install locally in 30 seconds."
- "Ship a public workspace with rate-limited reads (no writes) at demo.cctx.dev. Costs $5/mo Turso. Anthropic API not touched — it's a search demo, not an agent demo."

---

## Axis 2: Differentiation (grounded in Posts 1, 2, and precedent strength)

Would this plan cut through the noise, or would it drown? Does it lean on cctx's minimalism/category thesis, or could it describe any memory tool? Is the precedent cited a genuinely comparable launch, or generic "companies have done this"?

### Tier 1 — drowns in the noise
Could be any dev tool's launch plan. Precedent is generic or missing. Positioning in the demo restates the category without claiming it. Ignores the minimalism thesis entirely.

**Exemplars (score = 1–3):**
- "Launch on Product Hunt with a polished landing page. Many dev tools have done this."
- "Post on Twitter and hope it goes viral."
- "Submit to Awesome Lists and Dev.to."
- Precedent: "Lots of open-source projects launch on GitHub" (no named comparable, no mechanism).

### Tier 5 — credible but undifferentiated
Names a real comparable. Channel + demo are plausible. But the plan could describe Mem0's launch, Zep's launch, or any memory tool equally well. Minimalism thesis is absent from the demo framing.

**Exemplars (score = 4–6):**
- "Launch on HN with the title 'cctx: open-source memory for AI agents.' Precedent: Mem0 got traction on HN."
- "Free MIT + GitHub Sponsors. Like htmx, we'll grow through word of mouth."

### Tier 10 — cuts through with a thesis competitors can't copy
The plan weaponizes the minimalism claim ("a SQLite file your team shares") or the cloud-context category claim as the core hook. The precedent is specifically-transferable (not just "they also launched on HN") and the comparison explains *why that mechanism applies here.* Competitors would have to contradict their own positioning to run the same play.

**Exemplars (score = 9–10):**
- "Show HN: 'cctx — cross-AI memory in 9 REST endpoints and a SQLite file.' Precedent: Plausible's 'I self-host this' blog posts worked because the self-host path was genuinely usable. cctx's installer gives the same lived experience — users will post 'I installed this in 30 seconds' within a week. Anthropic memory_stores cannot run this play because they have no self-host path."
- "Raycast-store submission + a 60-second demo video showing Claude Code → Cursor → Codex context portability. Precedent: Raycast grew through Twitter + Alfred-users-migrating posts because it was visibly more polished than Alfred. cctx's minimalism is the same move against Mem0/Zep — the 'just a file' pitch is visually demonstrable in a terminal recording."
- "Dual-license (MIT + commercial for >$10M ARR companies). Precedent: SQLite-the-project showed that 'public domain code + optional paid support' compounds for decades. cctx's minimalism thesis makes the same promise — one file, grep-able, backupable with `cp`. No competitor leaning on a managed service can make this promise."

---

## Axis 3: Internal coherence (the plan as a system)

Do the six fields reinforce each other, or do they contradict each other? A coherent plan has the demo_mechanism, business_model, pricing, most_effective_demo, followership_channel, and reasoning all pulling in the same direction. An incoherent plan picks one strong tactic in isolation and pairs it with choices that undermine it.

### Tier 1 — fields contradict each other
The business model fights the channel. The demo fights the pricing. The precedent doesn't match the plan. Example: "Commercial SaaS at $99/mo + launch on r/selfhosted" — the audience is exactly the people who won't pay. Or "MIT open source + cold outbound to Fortune 500 CIOs" — the channel doesn't match the model.

**Exemplars (score = 1–3):**
- "Commercial SaaS $49/mo + launch on r/selfhosted + precedent: Plausible." (Plausible's audience overlapped with self-host; a pure commercial tier does not.)
- "MIT open source + launch on a paid founder podcast + precedent: Linear." (Linear is commercial; the analogy doesn't hold.)
- "Donation-ware only + target enterprise buyers + precedent: SQLite." (Enterprise buyers don't donate.)

### Tier 5 — mostly consistent with one weak link
Five of the six fields reinforce each other; one is a stretch. Example: open-core model + HN launch + reasonable demo + but pricing is unrealistic for the stated audience.

**Exemplars (score = 4–6):**
- "Open-core (MIT kernel + $19/mo managed sync) + HN launch + Loom demo + precedent: Plausible. Pricing: $19/mo seems steep for a solo dev." (Mostly coherent, one weak link.)

### Tier 10 — every field reinforces every other
The business_model explains why the channel will work. The pricing fits the precedent. The demo_mechanism IS the proof-point the channel needs. The reasoning ties them into a single argument. A reader can't swap one field without breaking two others. The plan feels inevitable, not assembled.

**Exemplars (score = 9–10):**
- "MIT open source + optional $9/mo managed Turso tier for teams that don't want to self-host + Show HN demo showing the installer + precedent: Plausible. Self-host is real, managed is opt-in revenue, HN rewards 'you can own this' pitches, demo shows the installer in 30 seconds. Every field supports 'you own your memory file.' Swap pricing to $49 and HN smells commercial; swap channel to LinkedIn and the open-source claim no longer matters."
- "Dual-license (MIT + $X/yr for companies >$10M ARR) + Raycast store + 60-second terminal recording showing context portability + precedent: htmx. Raycast audience values taste, the terminal recording demonstrates the minimalism thesis, dual-license matches the 'hobbyists free, companies pay' pattern htmx creator eventually adopted. Remove any field and another stops making sense."

---

## Anti-pattern word list — penalize on ALL axes

Any plan that uses 3 or more of these words should not score above 5 on any axis. These are hedge/marketing vocab that signals a generic plan:

`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`, `go-to-market strategy`, `growth hacking`, `viral loop`, `community-led growth`, `thought leadership`.

Report any flagged words found in the `anti_pattern_words` output field.

## Constraint-violation cap

If the plan violates any hard constraint in constraints.md — paid ads, cold outbound at scale, enterprise sales motion, requires new product features, ignores the $5/mo Anthropic cap, or requires a marketing hire — set `constraint_violation = true` and cap feasibility at 2 and differentiation at 4. A plan that violates constraints cannot be coherent no matter how clever the tactic.

## Output format

Return exactly one JSON object matching this schema. No prose, no markdown fences.

```json
{
  "feasibility": <integer 1-10>,
  "feasibility_reason": "<one concise sentence>",
  "differentiation": <integer 1-10>,
  "differentiation_reason": "<one concise sentence>",
  "coherence": <integer 1-10>,
  "coherence_reason": "<one concise sentence>",
  "constraint_violation": <true or false>,
  "constraint_violation_reason": "<one concise sentence — which constraint was violated, or why none was>",
  "anti_pattern_words": ["<flagged words in any text field, or empty array>"]
}
```

Integer scores only. No floats, ranges, or null. If unsure between tiers, pick the lower one — the distribution track is conservative.
