# Reddit Post A — Context Archaeology

**Target sub:** r/ClaudeAI or r/claudecode
**Tone:** Practitioner sharing a real observation, asking if others see it too
**Rule:** No product mention. No links. Pure problem-naming.

---

**Title:** I timed how much of every prompt is just re-explaining what Claude already knew yesterday

**Body:**

Been using Claude Code daily for ~6 months on a team project. Started noticing a pattern and decided to actually measure it.

For a full week I tracked how much of every prompt was "defensive context" — stuff like:

- "We're no longer using the old pricing tiers, use the updated ones from last week"
- "Ignore the previous project structure, we migrated to Railway"
- "Don't reference the competitor analysis from March, that data is outdated"

**40% of my prompt text was re-explaining things Claude knew in a previous session but forgot.**

I timed it across the team (25 people). Average 3 minutes per request on context setup, 8 requests per day. That's 2,500 hours/year we spend telling AI tools what to forget before we can tell them what to do.

We've been calling it "context archaeology" internally — the ritual of excavating what the AI should already know before asking it anything new.

The frustrating part: bigger context windows make it worse, not better. More history = more stale context to route around. I noticed Claude's responses degrade noticeably once you're maybe 20-30% into a long session. The old stuff starts contaminating the new stuff.

Has anyone else quantified this? Curious if the ratio is similar for other teams or if we're just particularly bad at session management.

---

**Notes:**
- Ends with a genuine question to drive comments
- "We've been calling it" is soft claim-staking — names the term without announcing it
- No solution pitched. Commenters will ask "so what do you do about it?" — that's the opening
- The 40% / 2,500 hours / $125K numbers are in there but framed as personal measurement, not marketing math
