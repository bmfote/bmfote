# Reddit Post C — Cross-Contamination

**Target sub:** r/ClaudeAI or r/claudecode
**Tone:** "PSA / heads up" — sharing a near-miss to help others avoid it
**Rule:** No product mention. No links. Problem only.

---

**Title:** PSA: if you use Claude for multiple customers in the same session, context from one client can bleed into another

**Body:**

Almost had a really bad day. Sharing this so someone else doesn't have to learn it the hard way.

One of our support engineers handles ~12 tickets/day in Claude. Usually keeps a session running because starting fresh means re-explaining our API rate limits, known bugs, escalation tiers, and SLA terms every single time.

9:00am — Ticket from Customer A, auth is failing. Engineer pastes their API config, debugs, resolves.

10:30am — Ticket from Customer B, completely unrelated timeout issue. Engineer asks Claude for troubleshooting steps. Claude's response includes a code snippet with **Customer A's endpoint URL and a partial API key** — pulled from the context still sitting in the session.

Engineer caught it before sending. This time.

The context from Customer A doesn't expire when you switch to Customer B. Claude can't tell the difference between "context I should reference" and "sensitive data from a different customer that happens to be in my window."

The workaround is obvious: start a fresh session for every ticket. But then you're back to re-explaining everything from scratch 12 times a day. We timed it — 36 minutes daily per engineer just on context setup.

So pick your failure mode:
- Long sessions → cross-contamination risk
- Fresh sessions → hours of daily re-explanation

There's no middle ground right now. The session model doesn't support "remember our company context but forget everything about the previous customer."

Has anyone found a decent pattern for this? We've tried system prompts, CLAUDE.md files, pasting a "company context" block — nothing really solves the fundamental issue.

---

**Notes:**
- "PSA" format gets clicks and saves on Reddit — people share these
- "Almost had a really bad day" is visceral and specific
- "Pick your failure mode" is the key framing — makes the problem feel structural
- "Has anyone found a decent pattern" is the genuine ask that drives comments
- Mention of CLAUDE.md files not working is important — surfaces the failure mode for file-based memory and opens the door for "there's a better approach" in comments
