# autoresearch — overnight positioning research for bmfote

> **Historical note:** This research validated "cloud context" as the category
> name and directly drove the rename from **bmfote** to **cctx**. All references
> to "bmfote" in this directory are preserved as-is — they reflect what the
> product was called when the experiments ran. The research itself is the reason
> the name changed.

Agentic iteration loop for bmfote's moat track. Adapts the `autoresearch-macos`
pattern (fixed time budget, single mutable target, crisp metric, keep-if-better,
log everything, run overnight) to agentic search over **positioning hypothesis
space** grounded in three frozen ground-truth posts.

**Night 1 is moat-only.** Code and mantra tracks will be added in later nights.

## What it does in one paragraph

Each experiment: the agent proposes one complete positioning hypothesis for
bmfote (a concrete operator persona + broken workflow, a minimalism-aligned
"how," a category-ownership "what," and a named counter-target + contradiction).
A judge scores it on a three-axis rubric (minimalism coherence / category
ownership / persona grounding), 1–10 per axis, anchored to tier-1/5/10 worked
exemplars baked into the rubric. Passing pitches are appended to
`tracks/moat/target.jsonl`. You wake up to a ranked list of ~40–80 scored
positioning hypotheses and pick the strongest to validate with a real operator.

Nothing in `engine/`, `README.md`, or Railway production is ever touched. The
agent has no file-edit tools at all — it emits structured JSON via forced
tool-use.

## Requirements

1. **Branch: AR.** The runner hard-fails on `main`/`master`.
2. **Claude Code installed.** The runner shells out to the real claude binary
   at `/Users/mattbatterson/.local/bin/claude` (bypassing the cmux shim) and
   uses your existing Claude Code OAuth. No `ANTHROPIC_API_KEY` required. No
   Anthropic API costs — all calls bill against your Claude Code subscription.
3. **`BMFOTE_REMOTE_DB` unset.** The runner refuses to run if it's set, so the
   harness never accidentally talks to Railway/Turso production.
4. **Python 3.12 venv at `.venv/`.** Already satisfied by the existing bmfote
   development environment.

## Quick reference

```bash
# Safety preflight only (no API calls)
.venv/bin/python -m autoresearch.runner --dry-run

# 1-experiment smoke test (~2–2.5 min)
.venv/bin/python -m autoresearch.runner --track moat --max-experiments 1

# 3-experiment rehearsal — refine, discover, refine (~6–8 min)
.venv/bin/python -m autoresearch.runner --track moat --max-experiments 3

# Focused overnight run (~40 experiments, ~90 min, $0 — uses Claude Code subscription)
.venv/bin/python -m autoresearch.runner --track moat --max-experiments 40

# Full overnight run (~80 experiments, ~2.5–3 hours, $0)
.venv/bin/python -m autoresearch.runner --track moat --max-experiments 80

# Release a stale lock after a crash
.venv/bin/python -m autoresearch.runner --release-lock
```

## Safety architecture

Seven defense layers:

1. **Branch guard** — `prepare.py` hard-fails unless HEAD is `AR`. `main`/`master`
   are blocked by name.
2. **Remote DB blocker** — refuses to start if `BMFOTE_REMOTE_DB` or
   `RAILWAY_ENVIRONMENT` is set.
3. **No file-editing surface** — the agent's only output is a structured JSON
   proposal via forced tool-use. There are no file-write tools available to it.
   The runner never touches `engine/`, `README.md`, or `CLAUDE.md` — it only
   writes to `autoresearch/state/` and `autoresearch/tracks/moat/target.jsonl`.
4. **No subprocess, no worktrees** — pure SDK calls. The "cmux claude
   subprocess" failure mode that kills recursive `claude -p` calls from inside
   a Claude Code session does not apply here.
5. **Ground-truth hash verification** — `prepare.py` reads the sha256 of each
   ground-truth post + rubric on every run. Editing any of them is a visible
   state change; the runner logs the new hashes on every startup.
6. **Kill-safe state** — lockfile is PID-based (`state/lock`), all JSONL writes
   are append-only with fsync, SIGINT/SIGTERM finish the current experiment and
   then exit cleanly. Stale lockfile → `--release-lock`.
7. **No writes to production** — runner never shells out to `railway`,
   `turso`, `gh`, or anything else. Only Anthropic SDK calls and local file I/O.

## Files

```
autoresearch/
├── README.md                       # this file
├── runner.py                       # driver loop — ENTRY POINT
├── prepare.py                      # safety preflight + lock + ground-truth hashing
├── eval_common.py                  # jsonl logger, best.json helpers
├── agent.py                        # SDK wrapper — propose_candidate tool
├── judge.py                        # SDK wrapper — score_candidate tool
├── state/
│   ├── experiments.jsonl           # every experiment, success or fail
│   ├── drift.jsonl                 # periodic re-score of current best
│   ├── lock                        # PID lockfile (present during active run)
│   └── best/
│       └── moat.json               # current top scorer (for drift alarm)
└── tracks/
    └── moat/
        ├── program.md              # agent instructions (dual-mode, 4 personas)
        ├── rubric.md               # three-axis scoring rubric — FROZEN
        ├── ground_truth/           # three user posts — FROZEN
        │   ├── post_1_minimalism.md
        │   ├── post_2_cloud_context.md
        │   └── post_3_shared_brain.md
        └── target.jsonl            # append-only survivor log — what to read in morning
```

Editing `rubric.md` or any `ground_truth/*.md` changes the hashes logged at
startup. Start a fresh run by `rm target.jsonl state/best/moat.json` (and maybe
`state/experiments.jsonl` if you want the experiment log cleared too).

## Promotion gate

A pitch is promoted (appended to `target.jsonl`) if:

1. `counter_target_valid == true` (judge validates the contradiction)
2. `min_axis_score >= 6` (no axis below 6 — prevents 10/10/3 "clever tweet" pitches)
3. `composite >= 8.0` (where composite = 0.35·minimalism + 0.30·category + 0.35·persona)

This is an **absolute-floor** gate, not monotonic improvement. Once the agent
hits the top of the scale, you still want subsequent good variations recorded
— the target.jsonl is a ranked list of good pitches, not a Darwin tournament.
`state/best/moat.json` separately tracks the single true top scorer for drift
alarm purposes.

## Drift alarm

Every 10 experiments the runner re-scores the current best pitch. If its
composite moves by more than 0.7 vs the original score, the runner logs
`alarm: true` to `state/drift.jsonl` and halts the run. This catches judge
drift — the thing that kills LLM-as-judge setups over long runs.

If drift is flagged, inspect `state/drift.jsonl`:
```bash
jq '.' autoresearch/state/drift.jsonl
```

## Morning review

After an overnight run, read the JSONL with `jq`:

```bash
# Count promoted pitches, group by persona channel
jq -s 'group_by(.channel) | map({channel: .[0].channel, count: length, top_composite: (map(.scores.composite) | max)})' \
   autoresearch/tracks/moat/target.jsonl

# Top 5 by composite score
jq -s 'sort_by(-.scores.composite) | .[0:5] | .[] | {persona, counter_target, composite: .scores.composite, why, how, what}' \
   autoresearch/tracks/moat/target.jsonl

# Refine vs discover — which mode produced stronger survivors?
jq -s 'group_by(.mode) | map({mode: .[0].mode, count: length, avg: (map(.scores.composite) | add / length), top: (map(.scores.composite) | max)})' \
   autoresearch/tracks/moat/target.jsonl

# Most common counter_targets
jq -r '.counter_target' autoresearch/tracks/moat/target.jsonl | sort | uniq -c | sort -rn

# Failed experiments (errors, violations, scored but not promoted)
jq 'select(.promoted == false)' autoresearch/state/experiments.jsonl | less
```

**Morning workflow**:

1. Glance at `state/experiments.jsonl` to see total experiments, promotion
   rate, and any errors.
2. Run the "top 5 by composite" query to see the best survivors.
3. Group by `channel` (SMB / dev-first / agencies / fractional) to see which
   persona category dominates. If one channel produces the strongest pitches,
   that's where to push first on validation calls.
4. Compare `refine` vs `discover` mode scores. If discover wins the top slot,
   the three-post thesis is underspecified and worth extending with new
   personas. If refine wins, the three posts are saying the right thing and
   you're sharpening delivery.
5. Pick 1–3 pitches to validate with a real person in the target persona via a
   phone call. **Do not auto-port anything into `README.md`.** The target.jsonl
   is a prioritized list of hypotheses, not ready-to-ship copy.

## Cost and wall-clock envelope

- **Monetary cost: $0.** All calls route through `claude -p` subprocess and
  use Claude Code's OAuth, billing against your Claude Code subscription.
- Agent call: ~60–80s per experiment (generates ~500 tokens of structured output)
- Judge call: ~50–70s per experiment
- Per experiment: ~130–150s wall-clock
- Night of 80 experiments: **~2.5–3 hours**
- Night of 40 experiments: **~90 minutes**

The claude CLI enables 1h prompt caching by default on Claude Code
subscription plans — the big static system block (~12k tokens of rubric +
3 posts) is cached across all calls. First call builds the cache, subsequent
calls read it. Cache telemetry is logged per-experiment in
`state/experiments.jsonl` under `eval_breakdown.agent_usage` and `judge_usage`.

CLI latency is higher than a pure SDK path (~130s/experiment vs ~25s/experiment
for the Anthropic SDK) because the CLI does an internal 2-turn model call
(haiku preprocessing + sonnet generation). The tradeoff: zero API cost in
exchange for slower wall-clock. Fine for overnight; overkill for interactive.

## Kill switch

While running:
- **Ctrl-C once** → finishes the current experiment and exits cleanly.
- **Ctrl-C twice** → immediate exit (lockfile may be stale — run `--release-lock`).
- **SIGTERM** → same as one Ctrl-C.

After a crash or reboot:
```bash
.venv/bin/python -m autoresearch.runner --release-lock
```

## Known limits

- **Night 1 is moat-only.** Code track (FTS query rewriting w/ MRR@10 eval
  against a snapshot.db) and mantra track (pairwise-preference 1-liner) are
  future work, both follow the same runner.py architecture but add subprocess
  + worktree isolation because they need real file editing.
- **Judge drift** is managed by the drift alarm but not eliminated. If the
  alarm fires, read `state/drift.jsonl`, tighten the rubric with more anchored
  exemplars, and restart.
- **No persona dedup yet.** If the agent repeatedly proposes "HVAC ops manager"
  variations, the target.jsonl will have multiple near-duplicates. Survivor
  feedback in the agent prompt helps but isn't hard deduplication. Filter in
  the morning with `jq group_by(.persona)`.
- **Counter-target diversity isn't enforced.** The agent tends to pick the
  best-fitting counter-target for each persona; over 80 experiments you'll get
  natural spread, but if the JSONL shows 60 "Anthropic memory_stores" entries,
  bump the rotation logic in `agent.py:propose_candidate`.
