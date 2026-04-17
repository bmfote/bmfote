# LinkedIn Post C — Cross-Contamination

**Angle:** Context rot is a security/compliance risk
**Goal:** Make the "mixing contexts" failure mode visceral. Don't pitch cctx.

---

Customer A's API credentials showed up in Customer B's troubleshooting response.

Not because of a data breach. Because of a support engineer using the same AI session for both tickets.

Here's what happened:

Ticket #1 (9:00am): Customer A's authentication is failing. The engineer pastes their API config into Claude, debugs the issue, resolves it.

Ticket #4 (10:30am): Customer B has a timeout issue. The engineer asks Claude for troubleshooting steps. Claude's response includes a code snippet with Customer A's endpoint URL and a partial API key — pulled from the context still sitting in the session.

The engineer caught it before sending. This time.

This is the dirty secret of long AI sessions: context doesn't expire. Customer A's data doesn't disappear when you move to Customer B. The AI can't tell the difference between "context I should use" and "context from a different customer that happens to be in the same window."

"Just start a fresh session for each ticket."

That's the workaround. But a fresh session means re-explaining your company's API rate limits, known bugs, escalation tiers, and SLA terms 12 times a day. 36 minutes daily, per engineer. $26,250/year for a 5-person team — in pure re-teaching time.

Pick your failure mode:
- Long sessions → cross-contamination risk
- Fresh sessions → $26K/year in re-explanation overhead

Spotify Engineering saw this too: "It tended to get lost when it filled up its context window." They're not a small team with a small budget. The problem is structural.

Context rot isn't a feature gap. It's an architecture gap. And right now, every team using AI for customer-facing work is choosing between data leakage and lost productivity.

---

**Notes:** The security angle makes this shareable to compliance/legal audiences, not just engineering. The "pick your failure mode" framing makes the problem feel inescapable — which it is, without persistent cross-session memory.
