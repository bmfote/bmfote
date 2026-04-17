# LinkedIn Post B — Stale Data Risk

**Angle:** Context rot costs deals, not just time
**Goal:** Escalate from "annoying" to "dangerous." Don't pitch cctx.

---

Our AI-generated proposal cited a feature we deprecated 3 months ago.

"Real-time Salesforce sync" — changed to batch-only in March.
"Free 10GB storage" — reduced to 5GB in May.
"99.99% SLA" — downgraded to 99.9% in April.

The enterprise prospect's technical team tested the real-time sync claim.
Discovered it was batch-only.
$180K deal. Stalled for 3 weeks.

The Sales Enablement Manager who drafted it did nothing wrong. She used the same AI tool she's used for 7 months. The tool remembered every feature description from every prior proposal — including the ones that were no longer true.

The AI didn't forget. It remembered too much.

That's context rot. Not forgetfulness — contamination. Old data doesn't disappear when you add new data. It accumulates until the AI confidently cites your own outdated specs back to you.

"Just maintain a source of truth document."

Sure. But Uncle Bob said it best: "One of the problems with a big context window is that it remembers too much." Your source-of-truth doc is one signal in a sea of 7 months of old proposals, product discussions, and superseded specs. BM25 doesn't know which one is current.

The math: 720 proposals/year. 25% go stale within 6 months. Internal rework: $12K. Customer-facing delays: $21K. Damaged deals: $48K.

$81,000/year.

Context rot isn't an AI quality problem. It's a business risk that scales with how fast you ship.

---

**Notes:** The $180K deal story is the hook — leads with consequences, not theory. The "she did nothing wrong" line is important: this isn't user error.
