# cctx

<h4 align="center"><b>Cloud context for AI agents.</b> One SQLite file across Claude Code, Cursor, the Messages API, and Managed Agents. Hooks auto-capture. FTS retrieves in &lt;100ms. Like Dropbox moved files to the cloud, cctx moves AI context to the cloud.</h4>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL%203.0-blue.svg" alt="License"></a>
  <a href="package.json"><img src="https://img.shields.io/badge/version-0.11.7-green.svg" alt="Version"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.12-brightgreen.svg" alt="Python">
  <img src="https://img.shields.io/badge/node-%E2%89%A518-brightgreen.svg" alt="Node">
  <img src="https://img.shields.io/badge/Powered%20by-Turso-4FF8D2.svg" alt="Powered by Turso">
</p>

<p align="center">
  <a href="#the-problem-your-ai-is-in-silos">Problem</a> •
  <a href="#vs-anthropic-managed-agents-memory-stores">vs Managed Agents</a> •
  <a href="#install">Install</a> •
  <a href="#key-features">Features</a> •
  <a href="#proof-one-memory-multiple-agent-identities">Proof</a> •
  <a href="#use-from-any-agent-sdk">Agent SDKs</a> •
  <a href="#for-teams--the-shape-of-whats-next">Teams</a> •
  <a href="#host-your-own-server">Self-Host</a>
</p>

---

## Install

If you already have a cctx server running, one command wires up Claude Code:

```bash
npx cctx setup --url https://your-cctx-server --token <API_TOKEN>
```

Restart Claude Code. Memory is now auto-captured and auto-recalled across sessions. No server yet? → [Host your own](#host-your-own-server) (~5 minutes).

---

## The problem: your AI is in silos

Every AI tool you run lives in its own context.

- **Claude Code** on your laptop has its own history in `~/.claude/`.
- **Cursor / Windsurf** have their own local histories, inside the app.
- A **Messages API** script you ran yesterday stored nothing, anywhere.
- A **Managed Agents** session runs in Anthropic's cloud and has no idea any of the above exist.

None of them can see into the others. That's not a bug in Anthropic's design — it's a consequence of each product being built by a different team for a different job. The result: you tell every tool the same things every day, and nothing compounds.

**cctx is cloud context — one shared memory across all of them, in a SQLite file you own.** Every Claude Code turn, every Messages API call, and every Managed Agents run reads from and writes to the same searchable store. Ask any agent on any surface *"what was the ICP we agreed on last Tuesday?"* and it finds the answer no matter where the original conversation happened.

---

## vs. Anthropic Managed Agents memory stores

Anthropic shipped built-in memory stores for Managed Agents in April 2026. They're good — auto-invoked, with versioning, redact, and a console UI cctx doesn't have. But they only connect one silo (Managed Agents) to itself. cctx is the bridge across all four.

| | Managed Agents memory stores | cctx |
|---|---|---|
| Auto-invoked during a session | ✅ | ✅ (Claude Code hooks) |
| Versioning, redact, console UI | ✅ | ❌ |
| Claude Code history | ❌ | ✅ |
| Cursor / Windsurf history | ❌ | ✅ (via MCP) |
| Messages API agents | ❌ | ✅ (`cctx-client`) |
| Managed Agents sessions | ✅ | ✅ (`cctx-agent` CLI) |
| Bridge between all four surfaces | ❌ | ✅ |
| Your data, your infra | ❌ | ✅ |
| Multi-user / team-shared (architecture) | ❌ | ✅ (`workspace_id`; UI coming) |

**Use Managed Agents memory stores** if your agents live entirely inside `/v1/sessions` and you want Anthropic to manage versioning and redact for you.

**Use cctx** if you want cloud context across every surface, in a SQLite file you own, survivable if you leave any one vendor. memory_stores is a managed black box — you can't back it up with `cp`, grep it when retrieval fails, or inspect the file.

---

## Key Features

- 🌉 **Cloud context, one file** — a SQLite file you own. Hooks auto-capture every turn from Claude Code; MCP serves the same memory to Cursor, the Messages API, the Agent SDK, and Managed Agents. No vector DB, no framework, no orchestration.
- 🪝 **Zero-glue for Claude Code** — a `UserPromptSubmit` hook auto-records every turn and a `Stop` hook finalizes each session; MCP recall tools are registered automatically so the agent can pull memory on any turn without ceremony.
- 🐍 **Any Python agent, same surface** — `pip install cctx-client` gives Messages API and Agent SDK agents the same recall + write loop Claude Code gets for free.
- 🧠 **Agent-initiated writes** — agents call `remember()` to persist what matters, not just passively recall.
- 🔒 **Your Turso, your token, your data** — self-hosted, bring-your-own-bearer. AGPL server (no closed-SaaS re-hosts) + MIT client (drop into any agent codebase, proprietary or not).

Five MCP tools ship out of the box: `search_memory`, `find_error`, `get_context`, `get_recent`, `remember`. Retrieval is SQLite FTS5 with BM25 ranking — <100ms. No vector DB, no embedding pipeline, no re-index step.

---

## How It Works — cloud context in four steps

1. **Write** — every Claude Code turn is captured by a `UserPromptSubmit` hook (with a `Stop` hook finalizing the last turn on session end) and streamed to a Turso database. Non-Claude-Code agents do the same via `cctx-client`, or by calling `remember()` mid-turn as an MCP tool.
2. **Search** — a FastAPI server exposes BM25 full-text search over every message, session, and tool call you've ever had with any agent.
3. **Recall** — five MCP tools are auto-registered in Claude Code and reachable over HTTP by any MCP-speaking agent (Cursor, Managed Agents, custom Agent SDK apps).
4. **Bridge** — because recall is HTTP + MCP and writes are SDK-based, the same memory is reachable from every surface an agent can run on. No surface owns it.

See [`CLAUDE.md`](CLAUDE.md) for architecture details — schema, FTS5 triggers, embedded-replica vs direct-Turso modes.

---

## Proof: one memory, multiple agent identities

A live three-run reproduction — **one shared store, two different agent identities, zero SDK glue** — using n8n as the orchestration layer, Anthropic Managed Agents as the runtime, and cctx as the substrate.

**Short version:** A brand-new Agent B with zero prior sessions correctly surfaced the work of a different Agent A via 15 MCP calls against cctx, reconstructed a full history it had never seen, and even flagged garbage rows from an earlier debug session as "known artifacts."

Full reproduction — ten-node n8n workflow, exact request headers, the three-run table, and prerequisites — is in [`docs/n8n-proof.md`](docs/n8n-proof.md). Captured in commit [`438d91b`](https://github.com/bmfote/bmfote/commit/438d91b).

**The substrate is the store, not the agent** — memory is portable across agent identities, not tied to any one of them.

---

## What the installer does

- Registers an MCP server (`cctx-memory`) that exposes 5 memory tools to Claude Code
- Installs hooks at `~/.claude/hooks/cctx-*.sh` for automatic session sync
- Writes `~/.claude/cctx.env` with the URL and token
- Merges hook entries into `~/.claude/settings.json`

**Prerequisites:** [Claude Code](https://claude.com/claude-code) installed on this machine, and a running cctx server. Don't have a server? See [Host your own](#host-your-own-server) — ~5 minutes. Safe to re-run; run once per machine.

---

## Use from any agent SDK

cctx isn't Claude-Code-only. If your agent runs on the Messages API, the Claude Agent SDK, or Anthropic Managed Agents, install the Python client and you get the same recall + write surface with no code specific to Claude Code.

```bash
pip install -e ./client
export CCTX_URL=https://your-cctx-server
export CCTX_TOKEN=...
```

### Anthropic Managed Agents — the hardest silo to bridge

Managed Agents don't expose client-side hooks, so the integration flips: the agent itself calls `remember` and `search_memory` as MCP tools against cctx. cctx ships a `cctx-agent` CLI that handles the whole wiring — vault + credential, environment with `allowed_hosts`, agent config with `mcp_servers` + `mcp_toolset` + `always_allow` — in one command.

```bash
# Create a memory-only agent wired to cctx (idempotent — reruns are no-ops)
cctx-agent create \
  --name "my agent" \
  --system "You are a memory retrieval agent backed by cctx."

# Run it with a prompt; returns the final agent response
cctx-agent run <agent_id> "What did we decide about Acme last week?"

# Audit or retrofit an agent created elsewhere
cctx-agent doctor <agent_id> --fix
cctx-agent list
```

The CLI reads `CCTX_URL`, `CCTX_TOKEN` (from `npx cctx setup`), and `ANTHROPIC_API_KEY` from your shell. Shared resources — a `cctx-default` vault and `cctx-default-env` environment — are discovered by name and created on first use, so there is no separate setup step.

All paths write into the same `messages` table as Claude Code sessions. See [`client/README.md`](client/README.md) for the full surface, failure semantics, and limitations.

### Claude Agent SDK — no glue code

Plug the cctx MCP server into options for reads, register hooks for writes, done:

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

The agent gets `search_memory`, `find_error`, `get_context`, `get_recent`, and `remember` as tools automatically, and every user prompt + tool call is recorded back.

### Messages API — the full loop

Day 1 and Day 2 of an agent that continues its own research:

```python
import anthropic
from cctx_client import Client, record_exchange

cctx = Client()
session = cctx.session(project="research-agent")

# 1. Recall prior memory
prior = session.recall("competitor pricing research", limit=10)

# 2. Run the turn with that context in the system prompt
ac = anthropic.Anthropic()
user = "Continue from where we left off."
response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    system=f"You are a research agent.\n\n{prior}",
    messages=[{"role": "user", "content": user}],
)

# 3. Write the new turn back
record_exchange(session, user, response)
session.close()
```

Or let the agent choose when to recall by exposing cctx as **tools it can call mid-turn**:

```python
from cctx_client import TOOL_SPECS, handle_tool_use

response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    tools=TOOL_SPECS,    # search_memory, find_error, get_context, get_recent, remember
    messages=[{"role": "user", "content": "What did we decide about Acme last week?"}],
)
# Handle any tool_use blocks with handle_tool_use(block, client=cctx)
```

---

## For teams — the shape of what's next

cctx is currently a single-user primitive with multi-user *architecture*. The `workspace_id` column landed recently; the surface area to use it has not. If you're running a Claude-centric team of 1–5 people who are comfortable with a self-hosted server, cctx is deployable today. Beyond that, the gaps below are the roadmap.

**Works today**
- Multi-tenant row isolation at the database level (`workspace_id` on every message)
- Self-hosted deployment to any Docker host your team can reach
- Shared bearer token across all team members
- One store, N agents — different Claude Code machines, Managed Agents sessions, and Messages API scripts all see the same pool

**Not shipped yet**
- Team-invite flow / per-user bearer tokens with role-based access
- Web dashboard for non-technical users
- ChatGPT and Copilot adapters (Claude-speaking tools only today)
- Author attribution beyond session metadata
- Audit log / change history

If any of the gaps above would block your team, [open an issue](https://github.com/bmfote/bmfote/issues) — the team direction is the explicit next phase and your use case will shape what ships first.

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

(Non-macOS install instructions: https://docs.turso.tech/cli/installation)

### Step 1 — Clone the repo

```bash
git clone https://github.com/bmfote/bmfote && cd bmfote
```

Keep this shell open. Every command below runs from inside this directory.

### Step 2 — Create a Turso database

```bash
turso db create cctx-memory
turso db show cctx-memory --url              # -> libsql://...
turso db tokens create cctx-memory --expiration none
```

Save the URL and token. You'll pass them to the server as environment variables.

### Step 3 — Apply the schema and generate an API token

```bash
turso db shell cctx-memory < engine/schema.sql
openssl rand -hex 32    # save this — every client needs it
```

### Step 4 — Deploy the server

The server is a single `Dockerfile`. Pick your provider.

> **All commands below must be run from inside the cloned `cctx` directory** (same shell as Step 1). Your provider CLI needs to see the `Dockerfile`.

<details>
<summary><strong>Railway</strong></summary>

```bash
railway init
railway service                                # link or create a service
railway variables --set TURSO_DATABASE_URL=libsql://...
railway variables --set TURSO_AUTH_TOKEN=...
railway variables --set API_TOKEN=...
railway up
railway domain                                 # your public URL
```

Railway distinguishes **projects** from **services**. `railway init` creates a project but does not always link a service. If later commands complain `No service linked` or `No services found`, run `railway service` and pick or create one, then re-run the failing command.
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

- **`railway up` says `Dockerfile not found` or `no build context`** — you're not inside the cloned `cctx` directory. `cd` into it and retry.
- **`railway up` / `railway variables` says `No service linked` or `No services found`** — run `railway service`, pick or create a service, then re-run the failing command.
- **`fly launch` offers to generate a Dockerfile** — decline. The repo already ships one; make sure you ran `fly launch` from inside `cctx/`.
- **`turso db shell` errors on `engine/schema.sql: No such file`** — you're not in the repo root. `cd cctx` and retry.
- **`curl /health` returns connection refused** — the container failed to start. Check provider logs; the most common cause is a missing `API_TOKEN`, which makes the server fail closed.
- **`curl /api/stats` returns 401** — the `API_TOKEN` on the server does not match the token in your `Authorization: Bearer ...` header.
- **`/api/stats` returns zeros or empty** — schema was not applied. Re-run `turso db shell cctx-memory < engine/schema.sql` from the repo root.

</details>

---

## Local development

```bash
source .venv/bin/activate      # Python 3.12
python -m engine.server        # starts on PORT from .env (default 8026)
```

Local dev uses an embedded libSQL replica at `engine/local-replica.db` that syncs to your Turso database. Auth is optional locally (no `API_TOKEN` required).

---

## License

cctx uses a split license:

- **Server, hooks, installer, and CLI** — [GNU AGPL-3.0](LICENSE). If you modify cctx and run it as a network service, AGPL-3.0 requires you to make your modified source available to your users.
- **Python client library** ([`client/`](client/)) — [MIT](client/LICENSE). Free to embed in proprietary agent code with no copyleft obligations.

The server is AGPL so commercial re-hosters can't take cctx, add private features, and compete as a closed SaaS. The client is MIT so you can drop it into any agent codebase — proprietary or not — without license friction.

---

**Built with FastMCP** | **Powered by Turso (libSQL)** | **AGPL-3.0 + MIT (split)**
