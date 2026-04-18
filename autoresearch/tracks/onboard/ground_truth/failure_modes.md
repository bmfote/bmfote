# cctx Install Failure Modes — Bounce Catalog

A user who bounces during install never sees the recall improvement shipped in Night 3. Every proposal in this track must cite at least one failure mode from this catalog (or document a new one) and show how the patch shortens or eliminates it.

Failure modes are ordered roughly by how often they swallow first-run users. Numbers are ordinal, not priorities — the judge scores whatever mode you tackle.

## F1. Missing `--url` or `--token` — silent-ish early exit

**Trigger:** User pastes the `npx cctx setup` one-liner from the README without substituting real values, or copies a partial command from a tweet/screenshot.

**Today's behavior:** `setup.sh` prints `ERROR: --url and --token are required.` and a usage hint, exits 1. That's functional but the error comes from a shell script that's been invoked four layers deep (`npx` → `cli.js` → `spawn bash` → `setup.sh`), and the exit status doesn't always propagate cleanly on Windows terminals wrapping WSL.

**Gap:** The user has no idea whether the token they pasted is malformed or missing — the error looks the same either way. No hint that the token comes from `npx cctx deploy`.

## F2. Wrong `--url` — `api/stats` probe fails opaquely

**Trigger:** User pastes a hostname missing `https://`, or includes a trailing path (`/mcp`), or pastes the Turso URL instead of the Railway app URL.

**Today's behavior:** Step [2/6] prints `ERROR: Could not reach $CCTX_URL/api/stats` and exits 1. A corporate proxy or wrong host gets the secondary error `API responded but didn't return valid stats JSON.`.

**Gap:** No distinction between DNS failure, TLS failure, 401 (bad token), 404 (wrong path), and 502 (app booting). Retry guidance is absent — a Railway cold start can take 20s but the 10s `--max-time` kills the probe first.

## F3. Claude Code not in PATH

**Trigger:** User installed Claude Code via the `.app` bundle on macOS and never linked `/usr/local/bin/claude`, or is on WSL where the host-side Claude Code binary isn't visible to the Linux shell, or has a non-standard `$PATH` under `~/.zprofile` vs `~/.zshrc`.

**Today's behavior:** Step [1/6] prints `ERROR: Claude Code CLI not found.` + docs link, exits 1.

**Gap:** Doesn't suggest `which claude`, doesn't probe standard install locations (`/Applications/Claude.app/Contents/MacOS/...`), doesn't detect WSL and print WSL-specific guidance.

## F4. `claude mcp add` silently no-ops

**Trigger:** User's `~/.claude/` is read-only (common in locked-down enterprise setups), or their Claude Code version predates user-scope MCP support, or a conflicting `cctx-memory` entry already exists in project scope.

**Today's behavior:** `claude mcp add` returns 0. Step [3/6] prints `Added MCP server: cctx-memory (user scope)`. But the tool isn't actually registered.

**Gap:** No post-add verification (`claude mcp list | grep -q '^cctx-memory:'`). User finishes install, starts Claude Code, asks for `search_memory`, gets "tool not found," has no idea where it went wrong.

## F5. Hooks download fails behind corporate firewall

**Trigger:** `curl -fsSL https://raw.githubusercontent.com/cctx/cctx/main/hooks/...` blocked by a proxy that strips HTTPS or requires authentication.

**Today's behavior:** `exit 1` with `ERROR: Failed to download $hook from GitHub`. No fallback, no offline mode.

**Gap:** No `--hooks-dir` flag to point at a pre-downloaded tarball, no instruction to clone the repo and re-run from the checkout (which *is* supported via `HOOKS_SRC` detection but undocumented).

## F6. Hooks fire but fail silently

**Trigger:** `~/.claude/cctx.env` has a trailing newline mismatch, or the `CCTX_TOKEN` value contains a `$` that got interpolated by the user's shell, or `python3` isn't in PATH inside the hook's subshell.

**Today's behavior:** `cctx-post-compaction-context.sh` exits 0 on any connectivity failure (fail-open policy). User sees nothing in Claude Code — no error, no "memory available" reminder. They conclude install didn't work.

**Gap:** The fail-open design hides fatal misconfigs. No `cctx doctor` subcommand to probe the whole stack end-to-end.

## F7. Stale `~/.claude/cctx.env` overwrites a new deploy

**Trigger:** User deploys a new Railway instance (different token), re-runs `npx cctx setup --url NEW --token NEW`. The env file gets overwritten correctly. But the Claude Code process they already have open still has the old `CCTX_TOKEN` cached in its subprocess env.

**Today's behavior:** User thinks install succeeded. Hooks silently 401 against the new server. MCP tools return errors that look like server-side failures.

**Gap:** Step 7 (implicit) doesn't remind the user to restart Claude Code. The final `No restart needed` claim in stdout is wrong in this specific case.

## F8. `api/stats` returns non-standard JSON

**Trigger:** User deployed a fork of cctx that changed the stats response shape, or is behind a captive portal that returns HTML with 200 status.

**Today's behavior:** `KeyError: 'messages'` caught by the Python probe, prints `ERROR: API responded but didn't return valid stats JSON.`.

**Gap:** The error suggests "proxy or wrong host may be intercepting" but doesn't show the first 200 chars of the actual response body, which is what a user would need to self-diagnose.

## F9. No "first successful recall" signal

**Trigger:** Every install that completes cleanly (all 6 steps exit 0).

**Today's behavior:** Script exits with the "Setup complete" banner. No proof that the MCP tool is actually reachable from inside Claude Code — user has to start a new session, ask for `search_memory`, and see what happens.

**Gap:** No self-test of the MCP round trip. A user who bounces because "I installed it but nothing happened" has no way to see whether the fault is at the MCP layer, hooks layer, or API layer.

## F10. The README advertises `cctx docs` and `cctx doctor` that don't exist

**Trigger:** User reads the README, runs `cctx docs ingest <url>` or `cctx doctor`, gets `Unknown command: docs`.

**Today's behavior:** Exit 1 with a generic "Run cctx --help for usage" hint.

**Gap:** README drift vs `bin/cli.js`. The install process itself doesn't fail, but the first thing a user tries after install fails with a confusing error.

---

## Non-failure-modes (out of scope)

These bounces are real but not install-surface issues:
- Railway free tier sleeping (server-side, not install)
- User's Claude Code trial expiring (billing, not install)
- User's corp blocking Railway domains at the firewall layer (infra, not install — user already can't reach the server)
- MCP protocol version mismatches on old Claude Code builds (Anthropic-side)

Patches in those areas will be marked out of scope by the judge.
