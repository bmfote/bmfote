# The Winning Pattern — Silent-Failure Guards

This document is frozen ground truth. It describes the one patch shape that has been empirically shown to produce promotable install-surface improvements. Match this shape for your assigned mode.

## The pattern in one sentence

> **After a command that can silently fail (exit 0 while leaving the system in a broken state), insert a 3–8 line guard that detects the failure and prints an error message naming the exact next command the user should run.**

## Canonical example — `verify-mcp-registration`

This patch scored **composite 7.50** (time_to_value=8, failure_mode_coverage=7, minimalism=6, taste=9) and was promoted. It is the only patch that has ever promoted on this track.

### The insertion

**Anchor line** (exactly one match in `installer/setup.sh`):
```
  --header "Authorization: Bearer $CCTX_TOKEN"
```

**Insertion lines** (7 lines, inserted after the anchor):
```

# Verify MCP server was actually registered (claude mcp add exits 0 even on failure)
if ! claude mcp list 2>/dev/null | grep -q '^cctx-memory:'; then
  echo "  ERROR: MCP server registration failed."
  echo "  Check: claude mcp list — if empty, ~/.claude/ may be read-only or Claude Code outdated."
  exit 1
fi
```

### Why it wins — line by line

1. **Blank line at top** — separates from the preceding block. Matches existing file style.
2. **Comment naming the exact failure mode** — "claude mcp add exits 0 even on failure" is the F4 mechanism verbatim. The judge rewards rationale that appears in the code.
3. **Boolean check with `grep -q`** — reuses the existing `grep -c '^cctx-memory:'` pattern from line 94. Zero new patterns. Zero new dependencies.
4. **Two-line ERROR output** — first line names what broke, second line names what to run next. Indented two spaces to match the existing `echo "  ..."` pattern in all six numbered steps.
5. **`exit 1`** — hard stop at install-time instead of letting the user discover failure 15 minutes later inside Claude Code.
6. **`fi`** — closes the if. That's the whole guard.

### Why the judge praised each axis

- **time_to_value (8/10):** "Detects F4 misconfig at install-time with actionable diagnostic, eliminating one full retry cycle for users with read-only dirs or old Claude Code."
- **failure_mode_coverage (7/10):** "Cites F4 and handles primary triggers (read-only directories, outdated Claude Code) but may miss project-scope conflict edge case." Not 10 because one-mode coverage is rarely comprehensive; 7–8 is the realistic ceiling for a single-mode guard.
- **minimalism (6/10):** "Net +7 lines, no new dependencies, reuses existing patterns (grep, echo format), single focused check." The +7 lines caps minimalism at 6; net-negative diffs score higher, but install-hardening rarely produces them.
- **taste (9/10):** "Style matches exactly, description is one precise sentence, user-facing output gives exact next action, zero marketing language." This is where the patch distinguishes itself from structurally similar near-misses.

## Near-miss: what lost 0.7 points

A structurally identical patch (`verify-mcp-registration`, 8 added lines, same anchor region) scored **6.80** and did not promote. Same time_to_value, same failure_mode_coverage, same minimalism. Lost 2 points on taste because:

- The error message suggested `sudo` as a fix, which is not universally applicable (WSL, corporate Macs without admin rights).
- Description mentioned "fail fast if not found" — accurate but less precise than "actually registered … before declaring success."

The lesson: **error messages must be universally-applicable and name the specific command the user should run**. Not a category of fixes — a single command or a single check.

## Near-miss: what the F2 bounce taught us

Six patches in the F2 (API probe) region scored 6.40 to 7.30. None promoted. All failed the same way: to handle F2 cleanly you need to branch on DNS failure vs 401 vs 404 vs 502 vs cold-start timeout, and that branching inflates the patch to 15–33 lines, capping minimalism at 4–5. The lowest-minimalism F2 patch that scored well was a 2-line timeout extension (10s→25s) — but the judge scored failure_mode_coverage=4 because "extending timeout hides the problem instead of closing it."

**The takeaway: not every failure mode is guard-shaped.** If your assigned mode requires branching, either:
- Find a single check that catches the common case (e.g., check only for the most common failure: cold-start timeout), or
- Propose a narrower mode (`discover`) that reframes the target as a single-boolean check.

## Structural invariants of a guard that scores

| Constraint | Why |
|---|---|
| Insertion-only (0 removes) | Removing existing lines nearly always breaks context-line fidelity. Additions after an anchor are mechanically safe. |
| ≤ 8 inserted lines | More than 8 lines implies branching, which the rubric caps minimalism at 4. |
| Anchor line appears exactly once in target file | Otherwise the runner can't place the insertion deterministically. |
| Boolean check with `grep -q` / `test -f` / explicit exit-code test | Reuses existing shell idioms. New idioms cost taste points. |
| Error message on two lines: what broke + exact next command | Judge scores error_craftsmanship directly on this. |
| `exit 1` — hard fail at install-time | Soft-failing (just `echo ERROR`) lets the bounce happen later, undoing the time_to_value win. |

## What does not count as a guard

- Retry loops (add complexity without closing the underlying failure)
- Case statements branching on multiple error codes (blows up minimalism)
- New helper functions (new abstractions, low minimalism)
- Debug-mode `--verbose` flags (the bouncing user doesn't know to pass them)
- Progress spinners, ASCII art, emojis (taste-neutral at best, style drift at worst)
- Anything that modifies `engine/`, documentation prose, or package.json
