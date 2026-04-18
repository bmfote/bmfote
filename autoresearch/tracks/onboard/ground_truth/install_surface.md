# cctx Install Surface — Frozen Snapshot

This is the current install surface as of track creation. It is the artifact the agent proposes diffs against. Read it carefully — the patch must match real line numbers and real commands.

## Entry point

Users have exactly one on-ramp:

```bash
npx cctx setup --url <API_URL> --token <API_TOKEN>
```

`bin/cli.js` dispatches `setup` to `installer/setup.sh` via `spawn("bash", [script, ...rest], { stdio: "inherit" })`. The shell script is the real work.

## What setup.sh does (6 numbered steps)

1. **Parse args** — requires `--url` and `--token`. Exits 1 with a usage hint if either is missing.
2. **Verify Claude Code** — `command -v claude`. If missing, prints the docs link and exits 1. Prints `claude --version` on success.
3. **Test API connection** — `curl -sf --connect-timeout 5 --max-time 10 -H "Authorization: Bearer $CCTX_TOKEN" "$CCTX_URL/api/stats"`. If the HTTP call fails or the body isn't valid JSON with a `messages` key, exits 1.
4. **Configure MCP server** — `claude mcp remove -s user cctx-memory` (if one already exists), then `claude mcp add -s user --transport http cctx-memory "$CCTX_URL/mcp/" --header "Authorization: Bearer $CCTX_TOKEN"`.
5. **Install hook scripts** — for each of `post-compaction-context.sh`, `pre-compaction-context.sh`, `stop.sh`, `sync-transcript.sh`: copy from the local `hooks/` dir if running from a checkout, else `curl -fsSL "$GITHUB_RAW/$hook"` from `https://raw.githubusercontent.com/cctx/cctx/main/hooks/`. Write to `~/.claude/hooks/cctx-<hook>`. `chmod +x`.
6. **Merge hooks into settings.json** — Python heredoc strips legacy non-prefixed entries (pre-0.5 installs double-fire otherwise), then registers `UserPromptSubmit` → `cctx-post-compaction-context.sh` and `Stop` → `cctx-stop.sh` in `~/.claude/settings.json`.
7. **Write `~/.claude/cctx.env`** — `CCTX_URL=...\nCCTX_TOKEN=...`, chmod 600. Also strips legacy `export CCTX_*` lines from `.zshrc`/`.bashrc`/`.zprofile`/`.bash_profile` with `sed -i.bak`.

Final stdout:
```
Setup complete — cloud context is live.

  MCP server:  cctx-memory → $CCTX_URL/mcp/
  Hooks:       ~/.claude/hooks/cctx-*.sh
  Config:      ~/.claude/cctx.env
  Database:    $MSG_COUNT messages available

Start a new Claude Code session — cloud context is ready. No restart needed.
```

## What `bin/cli.js` adds beyond `setup`

- **`cctx status`** — GET `/api/stats`, prints URL + messages + sessions + last message.
- **`cctx search "query"`** — GET `/api/search?q=...`, prints type + date + project + snippet + uuid.
- **`cctx launch <name>` / `--save` / `--list` / `--remove`** — session bookmarks via `/api/bookmarks`. `launch <name>` spawns `claude --resume <session_id>`.
- **`cctx --help`** — usage text. No `cctx doctor` today; the README advertises one that doesn't exist.

Config resolution order: env vars `CCTX_URL` + `CCTX_TOKEN` first; fall back to `~/.claude/cctx.env`; if neither, print `"cctx is not configured on this machine."` and exit 1.

## Hook behavior (what install enables)

Hooks live at `~/.claude/hooks/cctx-*.sh`. All read `~/.claude/cctx.env` via `.` (POSIX source), with env-var override.

- **`cctx-post-compaction-context.sh`** (UserPromptSubmit) — fail-open: if `curl -sf ... /api/stats` errors inside 1 second, exits 0 silently. Otherwise: syncs new transcript messages (background), detects compaction via two mechanisms (legacy `agent-acompact-*.jsonl` file count + JSONL summary record scan), and on every prompt prints `"Cloud context available. MCP tools: ..."` plus the last 3 messages from the current project.
- **`cctx-stop.sh`** (Stop) — runs `cctx-sync-transcript.sh` in the foreground to capture the trailing assistant response.
- **`cctx-sync-transcript.sh`** — incremental sync using `~/.claude/hooks/.sync-markers/<session_id>` to track which line of the JSONL transcript was last POSTed. First sync grabs only the last 200 lines.
- **`cctx-pre-compaction-context.sh`** — disabled stub (`exit 0`). Exists only so old settings.json entries don't 404.

## First-run success path

After a successful install, the minimum bar for "working memory" is:

1. User opens a new Claude Code session.
2. User asks Claude to call `search_memory` (or Claude calls it autonomously).
3. The MCP tool returns a non-empty result drawn from their own prior conversations (the hook's `UserPromptSubmit` sync populated the DB; or the DB was pre-populated by a prior machine's install).

Zero of these steps involves the user running another command. The install is the only touch point.

## What install DOES NOT do today

- No `cctx doctor` subcommand. If hooks fail to fire, there's no built-in diagnostic.
- No check that `claude mcp add` actually succeeded (it exits 0 even when the config write hits a permissions wall on certain WSL configs).
- No test call to `/mcp/` after registration — `api/stats` is the only connectivity probe.
- No prompt to the user to start a new Claude Code session; only the final stdout line suggests it.
- No detection of a stale `~/.claude/cctx.env` shadowing a newly-provided `--url`/`--token` — the script always overwrites, but the old env vars in an already-running Claude Code process persist.
- No version pinning in the `curl | bash` path; a user running today sees `main` HEAD hooks regardless of which `cli.js` version they `npx`'d.
- No support for air-gapped or proxy-gated environments (the `curl` fallback requires `raw.githubusercontent.com`).
