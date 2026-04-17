# Reddit Post B — Stale Data Risk

**Target sub:** r/ClaudeAI or r/SaaS
**Tone:** War story. "This happened to us, sharing so you don't get burned."
**Rule:** No product mention. No links. Problem only.

---

**Title:** Claude confidently cited a feature we deprecated 3 months ago in a customer proposal. Almost lost a $180K deal.

**Body:**

Wanted to share this because I haven't seen anyone talk about this failure mode specifically.

We use Claude daily to draft proposals and one-pagers. Product team ships updates every 2 weeks, pricing changes quarterly. Standard SaaS stuff.

7 months in, we noticed Claude was randomly pulling in old product details:

- "Real-time Salesforce sync" — we changed that to batch-only back in March
- "Free 10GB storage" — dropped to 5GB in May  
- "99.99% SLA" — downgraded to 99.9% in April

Enterprise prospect's technical team ran a POC based on the proposal. Tested the real-time sync. It's batch-only. $180K deal stalled for 3 weeks while we rebuilt trust.

The person who drafted it didn't do anything wrong. Same workflow she'd used for 7 months. The problem is that old feature descriptions from earlier proposals don't disappear — they just accumulate alongside the current ones. Claude has no way to know which version is current.

"Just keep a source of truth doc and reference it" — we tried that. The doc is there. But so are 7 months of old proposals, product discussions, and superseded specs in the session history. The source of truth is one signal in a sea of outdated noise.

The faster you ship product updates, the faster this gets worse. We ship biweekly. Every release cycle adds another layer of stale context that Claude can't distinguish from current specs.

Anyone else running into this? We're trying to figure out if there's a systematic fix or if everyone's just manually proofreading every AI-generated doc.

---

**Notes:**
- War story format is Reddit gold — specific, consequential, no BS
- "Anyone else running into this?" drives engagement
- "Manually proofreading every AI-generated doc" surfaces the workaround everyone's using but nobody talks about
- The comment thread will naturally surface solutions — that's where cctx enters (in replies, not the post)
