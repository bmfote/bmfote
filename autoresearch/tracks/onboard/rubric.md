# Onboard Track — Judge Rubric (Silent-Failure Guards)

Score each proposed install-surface guard on four axes. Integer scores only (1-10). Lower tier if unsure.

## Axis 1: guard_pattern_fidelity (weight 0.25)

Does the patch match the canonical silent-failure guard shape? Boolean check → ERROR echo → `exit 1`, insertion-only, ≤ 8 lines, no branching.

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | Not a guard at all: retry loop, case statement, helper function, refactor. Or violates insertion-only constraint (removes existing lines). |
| Low | 3-4 | Guard-ish but structurally off: branches on multiple conditions, wraps the check in a function, or exceeds 8 inserted lines. |
| Mid | 5-6 | Recognizable guard with one quirk: maybe 9–10 lines, or adds a single helper variable, or the boolean check is slightly awkward. |
| High | 7-8 | Clean boolean check + single ERROR echo + `exit 1`. ≤ 8 inserted lines. Reuses existing idioms (`grep -q`, `test -f`, `[ -z ]`). Insertion-only. |
| Top | 9-10 | Identical shape to the canonical `verify-mcp-registration` pattern: comment naming the failure mechanism, tight boolean check, two-line ERROR (what broke + exact next command), `exit 1`, fits in ≤ 7 lines. |

## Axis 2: time_to_value (weight 0.30)

Does this patch move the north-star metric — wall-clock time from `npx cctx setup` to first non-empty `search_memory` result?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | Moves the metric the wrong way: adds commands, suppresses diagnostics, hides the failure so it happens later. |
| Low | 3-4 | Neutral: cosmetic, shaves trivial wall-clock time with no effect on decisions. |
| Mid | 5-6 | Shortens a common failure path: turns a downstream bounce into an install-time error with an actionable next step. |
| High | 7-8 | Eliminates one full retry cycle: detects the misconfig at install time, tells the user what to run, prevents them from chasing the problem in Claude Code later. |
| Top | 9-10 | Closes a failure mode wholesale: after this patch ships, a user hitting the targeted bounce gets a correct diagnostic in seconds instead of minutes. Defensible with one concrete example. |

## Axis 3: failure_mode_coverage (weight 0.25)

Does the patch cite a real failure mode from `<failure-modes>` and handle it cleanly?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | No failure mode cited, or cites an out-of-scope mode (billing, Anthropic-side, firewall). Or patch doesn't actually address what it claims. |
| Low | 3-4 | Cites a failure mode but only handles the cosmetic symptom. Or the fix hides the problem (e.g. extends a timeout) instead of closing it. |
| Mid | 5-6 | Cites one failure mode and handles the primary trigger. Doesn't cover edge cases but the common case works. |
| High | 7-8 | Cites one failure mode and handles the primary trigger plus realistic variants. Or cites two related modes and addresses both in ≤ 8 lines. |
| Top | 9-10 | Cites a failure mode and eliminates it entirely for the common case, OR introduces a valid `["new"]` mode with specific trigger + reproducible symptom. The rationale would survive code review. |

## Axis 4: error_craftsmanship (weight 0.20)

Does the error message name the exact next command the user should run, in terse and universally-applicable language?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | No error message, or error contains marketing language, jargon, or `sudo`. Tells the user to "contact support" or "see the docs." |
| Low | 3-4 | Error names what broke but not what to do about it. Or suggests a command that only works on one platform (WSL-only, macOS-only). |
| Mid | 5-6 | Error names what broke and suggests a fix, but the suggestion is a category ("check your permissions") instead of a specific command. |
| High | 7-8 | Two-line error: first line names what broke, second line names a specific diagnostic command. The command is universally applicable. |
| Top | 9-10 | Identical to the canonical pattern: `ERROR:` prefix with two leading spaces, names the exact failed check on line 1, names the exact next command to run on line 2. Zero marketing language. Matches existing `echo "  ..."` style exactly. |

## Anchor-uniqueness gate (pre-scoring)

If `anchor_line` does not appear exactly once in `target_file`, the runner fails the experiment before scoring. Agent proposals with ambiguous anchors are not scored at all.

## Scope violation (pre-scoring)

If `target_file` is outside the allowed list, cap all axes at 3. Allowed:
- `installer/setup.sh`
- `bin/cli.js`
- `hooks/post-compaction-context.sh`
- `hooks/stop.sh`
- `hooks/sync-transcript.sh`

## Anti-pattern words

Penalize ALL axes by -1 if 2 or more found in any text field (including `description`, `rationale`, `error_message`, `expected_impact`, `insertion_lines`):
`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`

## Validation gate

The runner constructs a unified diff from `anchor_line` + `insertion_lines`, applies it in a git worktree, and runs:
- `git apply --check` on the constructed diff
- `bash -n` on any modified `.sh` file
- `node --check` on any modified `.js` file

If any of these fail, the patch is not scored — the experiment records the failure reason and the patch is discarded.

## Composite score

```
composite = 0.25 × guard_pattern_fidelity + 0.30 × time_to_value + 0.25 × failure_mode_coverage + 0.20 × error_craftsmanship
```

## Promotion gate

- `composite >= 7.0`
- `min(all four axes) >= 6`
- Validation passes (anchor unique, diff applies, syntax OK)
- No scope violation
- Anti-pattern word count ≤ 1

All conditions must hold.
