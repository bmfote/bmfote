# LinkedIn Post A — Context Archaeology

**Angle:** The invisible tax every AI-using team pays
**Goal:** Name the problem. Don't pitch cctx.

---

I tracked how my team uses AI for a week.

Not the outputs. The inputs.

40% of every prompt is defensive re-explanation:

"Ignore last month's persona definitions — we pivoted to enterprise."
"Don't use the old pricing tiers."
"Use the new messaging framework, not the one you remember from three threads ago."

We've started calling it "context archaeology" — the ritual of excavating what the AI should already know before you can ask it anything new.

I timed it. 3 minutes per request, 8 requests per day, across 25 people.

That's 2,500 hours per year. At $50/hour fully loaded, $125,000 annually.

Not on AI subscriptions. Not on inference costs. On telling AI tools what to forget.

The ironic part: "just use a bigger context window" makes it worse. More history means more stale context to route around. @dbreunig measured significant degradation past 20% of the 1M token window on Opus 4.6. 80% of the window is actively harmful.

This isn't a prompt engineering problem. No few-shot example can tell the AI what to forget when stale information accumulates faster than you can invalidate it.

There's a name for this: context rot.

And right now, every team using AI is paying the tax without seeing the bill.

---

**Notes:** No product mention. Let the problem create the demand. The term "context archaeology" is coined — use it consistently to build recognition.
