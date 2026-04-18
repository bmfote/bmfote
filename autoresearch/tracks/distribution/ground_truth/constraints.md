# Distribution track — operator constraints (frozen ground truth)

These are the hard constraints every proposal must respect. Ignore them at your own risk — a plan that violates these is auto-rejected regardless of how clever the tactic is.

## The operator

- **One person.** Matt Batterson. No marketing team, no growth hire, no designer on retainer. Everything in the launch plan must be executable by one operator with a day job.
- **No ad budget.** Zero paid acquisition. No Google Ads, no LinkedIn Ads, no Twitter promoted posts. If a channel requires money to reach people, it's dead on arrival.
- **Technical, not marketing-native.** The operator writes code fluently and ships software, but does not naturally write marketing copy, run an email list, or operate a content calendar. Plans that require daily blogging, SEO flywheels, or heavy content production will not get executed.

## The product

- **cctx** (formerly bmfote) — a cloud context system for AI agents.
- **What ships today:** 9 REST endpoints, 5 MCP tools (`remember`, `search_memory`, `find_error`, `get_context`, `get_recent`), hooks-based auto-capture across Claude Code / Cursor / Messages API / Managed Agents / Codex, installer at `installer/setup.sh` that wires it all in one command, Turso backend + local embedded replica, SQLite FTS5 with BM25 ranking.
- **What is live:** Production endpoint at `https://bmfote-api-production-7a63.up.railway.app`. Free tier on Railway + Turso handles the current load. One npm package (`cloud-context`) already published.
- **Install today:** `curl | bash` via `installer/setup.sh`. Works on macOS. Requires a Claude Code install.

## The economics

- **Anthropic API spend is capped at $5/month** on the shared workspace — this is a hard ceiling. Any "free hosted demo" plan that burns API tokens at scale is disqualified.
- **Railway + Turso free tiers** handle ~50–100 light demo users before hitting paid limits. A viral launch that pulls 5,000 signups in a day would OOM the free tier within hours.
- **$0–50/month** is the sustainable monthly infra budget. A proposal that requires $500/mo infra to execute is wrong for this operator.
- **No venture funding.** No runway beyond what the operator can absorb from personal income. No "raise a seed then distribute" plans.

## The relevant comparables (use these as precedent sources)

Successful bottom-up developer-tool launches that worked with similar constraints. Your `precedent` field should name one of these (or a comparable) and explain what specifically transferred:

- **Plausible Analytics** — open-source MIT + paid hosted. Launched on HN, grew through privacy-focused positioning + "I self-host this" blog posts from devs. Works because the self-host path is genuinely viable.
- **Beeper / Matrix bridges** — single-operator launches to HN + Reddit r/MacOS. Worked because "chat apps unified" was a legible one-liner.
- **Fathom Analytics** — paid, but distribution came from co-founder blogging transparency (revenue numbers public). No ads.
- **htmx** — pure MIT, grew through "I rewrote my React app in htmx and it's smaller" testimonial posts. Works because the comparison target (React) is widely disliked.
- **Raycast** — free with paid tier, launched through a Twitter-first strategy targeting Alfred users. Works because it showed up fully polished and had macOS-native taste.
- **Obsidian** — free personal + paid sync. Grew through Zettelkasten community + YouTube walkthroughs (not paid). Paid tier converts on "I want my vault synced across devices."
- **Linear** — commercial SaaS, launched via hand-picked private beta and Twitter thread from Karri Saarinen. Growth through design-obsessed dev Twitter audience.
- **SQLite** itself — public domain, no ads, distribution via being genuinely better than the alternatives and letting operators rediscover it.

## What won't work (exclusions)

- **Paid ads of any kind.** Out.
- **"Go enterprise."** This is a bottom-up tool. Plans that require a sales motion, a quota, or a BDR are wrong.
- **"Hire a marketing person."** Not an option in the current budget.
- **"Wait until we have features X, Y, Z."** The launch has to work with exactly what ships today (see product section). Anything requiring a new API, new surface, or new feature is auto-rejected.
- **"Become a YC company."** Valid advice for some founders, but outside the scope of this research — the question is distribution, not fundraising.
- **Cold outbound at scale.** The operator is not a salesperson and will not send 200 cold emails a day. Any plan that depends on high-volume manual outreach will not execute.

## What would count as a win

A successful distribution plan produces, within 60 days of execution:

- **500+ GitHub stars** OR **100+ active install events** from the installer (hooks-phone-home metric), AND
- **At least one of:** (a) front-page HN discussion, (b) 3+ organic "I installed cctx and it's great" posts from strangers, (c) one paying customer if the plan is commercial, or (d) one ecosystem integration request from an adjacent tool (Raycast / Alfred / Warp / etc.).

Anything less is the status quo.
