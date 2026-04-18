# Onboard Target Metric — "Zero to working memory"

The onboard track optimizes for a single end-to-end outcome. Every patch should move this metric in a direction the judge can defend.

## The metric

> **Time from `npx cctx setup` to first non-empty `search_memory` result from inside Claude Code, executed by a human who has never installed cctx before.**

This is the metric because it is the one a bouncing user experiences. It composes four latencies and three correctness checks:

1. Shell execution time (setup.sh runs all 6 steps).
2. Claude Code restart or new-session time (if required).
3. First hook fire (populates the DB or sees a prior DB).
4. First MCP tool call that the agent actually invokes.

And:

- MCP server is reachable.
- Hooks fired and synced at least one message.
- `search_memory("anything")` returns `len(results) > 0` against the user's own data (not a demo seed).

We cannot measure this in CI from a patch. The judge scores qualitatively, using this metric as the north star.

## What "shorter time to working memory" looks like in a patch

A patch helps the metric if any of these are true:

- **Fewer commands.** The user types one thing today (`npx cctx setup`). A patch that removes a second command (e.g., "also run `cctx doctor`") after a common failure helps. A patch that adds a second command the user must type to finish install hurts.
- **Fewer retries.** If a common failure mode today requires the user to try install twice (because the first attempt produced a misleading error), a patch that turns the first attempt into a success or an actionable error shortens time.
- **Faster failure when failure is inevitable.** A 10s timeout that becomes a 3s failure with a specific next-step hint is better than a 10s timeout that says "could not reach."
- **Higher signal in the success path.** Today's final "Setup complete" banner doesn't prove the MCP round-trip works. A patch that adds a self-test (e.g., one MCP call, one search, prints `OK` or surfaces the exact layer that broke) shortens time-to-confidence, even if it doesn't shorten wall-clock time.
- **Elimination of the bounce.** A patch that removes a failure mode entirely (e.g., detects the case in F3 and suggests the right fix) eliminates the bounce wholesale.

## What the metric punishes

- **Adding complexity.** A patch that adds 200 lines to `setup.sh` to handle a 0.1% edge case slows every future install and makes the script harder to audit. Minimalism axis catches this.
- **Moving failure later.** A patch that suppresses an early error so install "succeeds" but then fails silently at first hook fire is worse than the current behavior.
- **Over-engineering observability.** Adding a telemetry beacon, a structured log file, or a `--verbose` flag that no user knows to pass doesn't help the bouncing user.
- **Swallowing diagnostics.** A patch that replaces `ERROR: Could not reach $CCTX_URL/api/stats` with `Setup incomplete, please contact support` hides exactly the information a user needs to self-diagnose.

## What the metric is indifferent to

- Shaving a second off shell execution time.
- Cosmetic changes to stdout (unless they change the user's next action).
- Adding color, emoji, or ASCII art.
- Refactoring for readability without a user-visible behavior change.

The judge will mark those proposals low on the time-to-value axis even if they score well on minimalism and taste.

## Implicit constraints

- The install must remain safe to re-run (`setup.sh` says so on line 18, and that property must not regress).
- The install must still work on macOS (primary), Linux (secondary), WSL (best-effort). Windows native is not supported today and a patch is not expected to add it.
- No new runtime dependencies beyond what's already assumed: `bash`, `curl`, `python3`, `claude` CLI, `node` (for the `cli.js` wrapper). Any patch that requires `jq`, `yq`, `fzf`, or similar is out of scope unless the patch itself vendors the dependency.
- The patch cannot modify `engine/` — that's recall/code track territory.

## Outcome the morning-after review is looking for

A ranked `target.jsonl` of 20–50 promoted patches, each scored on the four axes (time-to-value, failure-mode coverage, minimalism, taste). The top 3–5 are candidates for a single bundled "install hardening" PR against the installer surface. Every promoted patch names a failure mode it addresses (from failure_modes.md or a newly-discovered one) and defends its expected impact on time-to-working-memory.
