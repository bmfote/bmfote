# cctx

<h4 align="center"><b>Cloud context for AI agents.</b> Like Dropbox moved files to the cloud, cctx moves AI context to the cloud. One SQLite file across Claude Code, Cursor, the Messages API, and Anthropic Managed Agents. Hooks auto-capture. FTS retrieves in &lt;100ms.</h4>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <a href="package.json"><img src="https://img.shields.io/badge/version-0.11.7-green.svg" alt="Version"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.12-brightgreen.svg" alt="Python">
  <img src="https://img.shields.io/badge/node-%E2%89%A518-brightgreen.svg" alt="Node">
  <img src="https://img.shields.io/badge/Powered%20by-Turso-4FF8D2.svg" alt="Powered by Turso">
</p>

<p align="center">
  <a href="#the-enemy-context-rot">Problem</a> •
  <a href="#the-fix-cloud-context-one-file">Fix</a> •
  <a href="#vs-every-other-memory-option">Counter-positions</a> •
  <a href="#install">Install</a> •
  <a href="#use-from-any-agent-sdk">Agent SDKs</a> •
  <a href="#for-teams">Teams</a> •
  <a href="#host-your-own-server">Self-Host</a>
</p>

---

## Three irreducible claims

- **A SQLite file you own** — not a managed black box. `cp` to back up, `grep` to inspect, delete a row to forget.
- **Hooks auto-capture** — every Claude Code turn, every Messages API call, every Managed Agents run writes to the same store. No paste, no prompt rituals.
- **FTS retrieves in <100ms** — BM25 ranking over standard SQLite FTS5. No vector DB, no embedding pipeline, no re-index step, no orchestration.

---

## The enemy: context rot

Every AI tool you run lives in its own context silo — Claude Code on your laptop, Cursor in its app, Messages API scripts in CI, Managed Agents in Anthropic's cloud. None share memory. The result is **context rot**: your AI tools get *worse* the more you use them because stale context accumulates faster than you can invalidate it.

### Three failure modes

| Failure mode | What happens | Annual cost (25-person team) |
|---|---|---:|
| **Context archaeology** | You spend 3 min/request telling AI what to ignore before telling it what to do | $125,000 |
| **Stale data in outputs** | AI-generated proposals cite deprecated features; an enterprise prospect tests a claim, finds it wrong, and a $180K deal stalls | $81,000 |
| **Cross-contamination** | Customer A's API keys appear in Customer B's troubleshooting response | $31,000 |
| | **Total** | **$237,000** |

### Why the obvious fixes fail

- **Bigger context windows** make it worse. [@dbreunig](https://x.com/dbreunig) measured "SIGNIFICANT decrease in performance at tokens > 20%" of Opus 4.6's 1M window. More tokens = more stale data drowning current context. Uncle Bob: *"one of the problems with a big context window is that it remembers too much."*
- **Managed memory** is a black box. You can't inspect what it remembers, debug why it forgot, or fix a wrong memory.
- **File-based memory** (`CLAUDE.md`) rots by accumulation. [@alxfazio](https://x.com/alxfazio): *"it's just updating the claude.md until it turns into a useless 6k line context rot."*
- **Per-seat licenses** silo memory by user. One person's context is invisible to the team.

Validated via 160 autoresearch experiments — 80 counter-positioning, 80 problem-definition — with 56 promoted at composite ≥8. Full evidence in [`docs/positioning.md`](docs/positioning.md) and [`docs/context-rot.md`](docs/context-rot.md).

---

## The fix: cloud context, one file

cctx is **cloud context** — drop-in experiential memory across every AI tool and every device, stored in a SQLite file you own. Every Claude Code turn, every Messages API call, and every Managed Agents run reads from and writes to the same searchable store.

Ask any agent on any surface *"what was the ICP we agreed on last Tuesday?"* and it finds the answer regardless of where the original conversation happened.

### Four steps

1. **Write** — a Claude Code `UserPromptSubmit` hook captures every turn; a `Stop` hook finalizes at session end. Non-Claude-Code agents use `cctx-client` or call `remember()` as an MCP tool.
2. **Search** — FastAPI exposes BM25 full-text search over every message, tool call, and saved thread.
3. **Recall** — five MCP tools (`search_memory`, `find_error`, `get_context`, `get_recent`, `remember`) auto-register in Claude Code and are reachable over HTTP by any MCP-speaking agent.
4. **Bridge** — because recall is HTTP+MCP and writes are SDK-based, the same memory is reachable from every surface. No surface owns it.

**Minimalism is the moat.** No vector DB, no framework, no orchestration engine. Every layer you remove makes the system harder to out-simple.

---

## vs. every other memory option

cctx is the only option that bridges all four surfaces — Claude Code, Cursor, Messages API, Managed Agents — into one file you own.

| | cctx | memory_stores | Mem0 | `CLAUDE.md` | ChatGPT/Copilot per-seat | LangGraph memory |
|---|---|---|---|---|---|---|
| Auto-invoked during session | ✅ hooks | ✅ | ⚠️ framework | ❌ manual | ✅ per-seat | ⚠️ graph-state |
| Claude Code history | ✅ | ❌ | ❌ | — | ❌ | ❌ |
| Cursor / Windsurf | ✅ MCP | ❌ | ❌ | — | ❌ | ❌ |
| Messages API agents | ✅ client | ❌ | ⚠️ | ❌ | ❌ | ⚠️ |
| Managed Agents | ✅ `cctx-agent` | ✅ | ❌ | ❌ | ❌ | ❌ |
| Bridge across all four | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Your data, your infra | ✅ | ❌ | ⚠️ hosted | ✅ | ❌ | ⚠️ |
| No vector DB / embedding pipeline | ✅ FTS5 | ✅ | ❌ | ✅ | ❌ | ❌ |
| Inspect with `grep`, back up with `cp` | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Team-shared architecture (`workspace_id`) | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |

**Use memory_stores** if all your agents live inside `/v1/sessions` and you want Anthropic to handle versioning and redact.

**Use cctx** if you want one memory across every surface, in a SQLite file you own, survivable if you leave any one vendor.

---

## Install

If you already have a cctx server running, one command wires up Claude Code:

```bash
npx cctx setup --url https://your-cctx-server --token <API_TOKEN>
```

Restart Claude Code. Memory is auto-captured and auto-recalled across sessions. No server yet? → [Host your own](#host-your-own-server) (~5 minutes).

**What the installer does:**
- Registers an MCP server (`cctx-memory`) that exposes 5 memory tools to Claude Code
- Installs hooks at `~/.claude/hooks/cctx-*.sh` for automatic session sync
- Writes `~/.claude/cctx.env` with the URL and token
- Merges hook entries into `~/.claude/settings.json`

**CLI surface** after install:

```bash
cctx status                         # connection + stats
cctx search "ICP we agreed on"      # BM25 search over all messages
cctx launch                         # interactive saved-thread picker
cctx launch --save "acme-deal"      # bookmark current session
cctx docs ingest <url|file> [tags]  # index reference docs alongside memory
cctx docs search "rate limit"       # FTS over ingested docs
```

Safe to re-run; run once per machine.

---

## Proof: one memory, multiple agent identities

A live three-run reproduction — **one shared store, two different agent identities, zero SDK glue** — using n8n as orchestration, Anthropic Managed Agents as runtime, and cctx as the substrate.

**Short version:** A brand-new Agent B with zero prior sessions correctly surfaced the work of a different Agent A via 15 MCP calls against cctx, reconstructed a full history it had never seen, and flagged garbage rows from an earlier debug session as "known artifacts."

Full reproduction in [`docs/n8n-proof.md`](docs/n8n-proof.md), commit [`438d91b`](https://github.com/bmfote/bmfote/commit/438d91b).

**The substrate is the store, not the agent** — memory is portable across agent identities, not tied to any one of them.

---

## Use from any agent SDK

cctx isn't Claude-Code-only. If your agent runs on the Messages API, the Claude Agent SDK, or Anthropic Managed Agents, install the Python client and get the same recall + write surface with no Claude-Code-specific code.

```bash
pip install -e ./client
export CCTX_URL=https://your-cctx-server
export CCTX_TOKEN=...
```

### Anthropic Managed Agents — the hardest silo to bridge

Managed Agents don't expose client-side hooks, so the integration flips: the agent itself calls `remember` and `search_memory` as MCP tools against cctx. cctx ships a `cctx-agent` CLI that wires the whole thing — vault + credential, environment with `allowed_hosts`, agent config with `mcp_servers` + `mcp_toolset` + `always_allow` — in one command.

```bash
# Create a memory-backed agent wired to cctx (idempotent)
cctx-agent create \
  --name "my agent" \
  --system "You are a memory retrieval agent backed by cctx."

# Run it
cctx-agent run <agent_id> "What did we decide about Acme last week?"

# Audit or retrofit an agent created elsewhere
cctx-agent doctor <agent_id> --fix
cctx-agent list
```

The CLI reads `CCTX_URL`, `CCTX_TOKEN` (from `npx cctx setup`), and `ANTHROPIC_API_KEY` from your shell. Shared resources (a `cctx-default` vault and `cctx-default-env` environment) are discovered by name and created on first use.

All paths write into the same `messages` table as Claude Code sessions. See [`client/README.md`](client/README.md).

### Claude Agent SDK — no glue code

```python
from claude_agent_sdk import ClaudeAgentOptions, query
from cctx_client import agent_sdk_hooks

options = ClaudeAgentOptions(
    mcp_servers={
        "cctx": {
            "type": "http",
            "url": "https://your-cctx-server/mcp/",
            "headers": {"Authorization": f"Bearer {CCTX_TOKEN}"},
        }
    },
    hooks=agent_sdk_hooks(project="ops-agent"),
)
async for msg in query(prompt="Continue yesterday's investigation", options=options):
    ...
```

The agent gets all five MCP tools automatically, and every user prompt + tool call is recorded back.

### Messages API — the full loop

```python
import anthropic
from cctx_client import Client, record_exchange

cctx = Client()
session = cctx.session(project="research-agent")

prior = session.recall("competitor pricing research", limit=10)

ac = anthropic.Anthropic()
response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    system=f"You are a research agent.\n\n{prior}",
    messages=[{"role": "user", "content": "Continue from where we left off."}],
)

record_exchange(session, "Continue from where we left off.", response)
session.close()
```

Or expose cctx as **tools the agent can call mid-turn**:

```python
from cctx_client import TOOL_SPECS, handle_tool_use

response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    tools=TOOL_SPECS,    # search_memory, find_error, get_context, get_recent, remember
    messages=[{"role": "user", "content": "What did we decide about Acme last week?"}],
)
```

---

## For teams

cctx is a single-user primitive today with multi-user *architecture* in place. The `workspace_id` column isolates memory at the database level. If you're running a Claude-centric team of 1–5 people comfortable with a self-hosted server, cctx is deployable today.

**Works today**
- Multi-tenant row isolation (`workspace_id` on every message, tool use, saved thread, and doc)
- Self-hosted deployment to any Docker host your team can reach
- Shared bearer token across all team members
- One store, N agents — different Claude Code machines, Managed Agents, and Messages API scripts all see the same pool

**Not shipped yet**
- Team-invite flow / per-user bearer tokens
- Web dashboard for non-technical users
- ChatGPT and Copilot adapters (Claude-speaking tools only today)
- Audit log / change history

If any of the gaps above would block your team, [open an issue](https://github.com/bmfote/bmfote/issues) — team direction is the explicit next phase.

---

## Host your own server

cctx is self-hosted. You need a Turso database and any Docker-compatible host (Railway, Fly, Render, bare Docker). ~5 minutes end-to-end.

<details>
<summary><strong>Full setup walkthrough</strong> — Turso CLI, DB, schema, deploy, verify, troubleshoot</summary>

### Step 0 — Install the Turso CLI

```bash
brew install tursodatabase/tap/turso
turso auth login
```

(Non-macOS: https://docs.turso.tech/cli/installation)

### Step 1 — Clone the repo

```bash
git clone https://github.com/bmfote/bmfote && cd bmfote
```

### Step 2 — Create a Turso database

```bash
turso db create cctx-memory
turso db show cctx-memory --url              # -> libsql://...
turso db tokens create cctx-memory --expiration none
```

### Step 3 — Apply the schema and generate an API token

```bash
turso db shell cctx-memory < engine/schema.sql
openssl rand -hex 32    # save — every client needs it
```

### Step 4 — Deploy

<details>
<summary><strong>Railway</strong></summary>

```bash
railway init
railway service
railway variables --set TURSO_DATABASE_URL=libsql://...
railway variables --set TURSO_AUTH_TOKEN=...
railway variables --set API_TOKEN=...
railway up
railway domain
```
</details>

<details>
<summary><strong>Fly.io</strong></summary>

```bash
fly launch --no-deploy
fly secrets set TURSO_DATABASE_URL=libsql://... \
                TURSO_AUTH_TOKEN=... \
                API_TOKEN=...
fly deploy
```
</details>

<details>
<summary><strong>Bare Docker</strong></summary>

```bash
docker build -t cctx .
docker run -d -p 8000:8000 \
  -e TURSO_DATABASE_URL=libsql://... \
  -e TURSO_AUTH_TOKEN=... \
  -e API_TOKEN=... \
  cctx
```
</details>

### Required environment variables

| Var | Required | Purpose |
|---|---|---|
| `TURSO_DATABASE_URL` | yes | `libsql://...` from `turso db show` |
| `TURSO_AUTH_TOKEN` | yes | from `turso db tokens create` |
| `API_TOKEN` | yes | shared secret clients send as `Authorization: Bearer` |
| `PORT` | no | defaults to 8000; providers set this automatically |

The server **fails closed**: it refuses to start without `API_TOKEN` in cloud mode.

### Verify

```bash
curl https://your-domain/health
curl -H "Authorization: Bearer $API_TOKEN" https://your-domain/api/stats
```

### Troubleshooting

- **`railway up` says `Dockerfile not found`** — you're not inside the cloned `cctx` directory.
- **`No service linked`** — run `railway service`, pick or create, retry.
- **`fly launch` offers to generate a Dockerfile** — decline; the repo ships one.
- **`curl /health` connection refused** — provider logs; usually missing `API_TOKEN`.
- **`curl /api/stats` returns 401** — token mismatch between server and client.
- **`/api/stats` returns zeros** — schema wasn't applied; re-run `turso db shell ... < engine/schema.sql`.

</details>

---

## Local development

```bash
source .venv/bin/activate      # Python 3.12
python -m engine.server        # starts on PORT from .env (default 8026)
```

Local dev uses an embedded libSQL replica at `engine/local-replica.db` that syncs to your Turso database. Auth is optional locally.

---

## License

cctx is [MIT](LICENSE) — server, hooks, installer, CLI, and the Python client library. Drop any piece into any codebase, proprietary or not.

---

**Built with FastMCP** | **Powered by Turso (libSQL)** | **MIT**
