# bmfote

<h4 align="center"><b>Cloud context for AI agents</b> — drop-in experiential memory that follows your agent across sessions, machines, and SDKs.</h4>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL%203.0-blue.svg" alt="License"></a>
  <a href="package.json"><img src="https://img.shields.io/badge/version-0.11.3-green.svg" alt="Version"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.12-brightgreen.svg" alt="Python">
  <img src="https://img.shields.io/badge/node-%E2%89%A518-brightgreen.svg" alt="Node">
  <img src="https://img.shields.io/badge/Powered%20by-Turso-4FF8D2.svg" alt="Powered by Turso">
</p>

<p align="center">
  <a href="#key-features">Features</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#use-from-any-agent-sdk">Agent SDKs</a> •
  <a href="#host-your-own-server">Self-Host</a> •
  <a href="#license">License</a>
</p>

> *Surprised no one has won on or is championing the idea of "cloud context" — essentially the drop-in experiential memory for agents that can be accessible across any agent or any device. Turso would be great for this.*

---

## Key Features

- ☁️ **Cloud-synced, locally fast** — your memory lives in Turso (libSQL); reads go through a local embedded replica
- 🧠 **Persistent agent memory** — every session, prompt, and tool call is searchable forever
- 🔌 **Works across Claude surfaces** — Claude Code, Messages API, Claude Agent SDK, Anthropic Managed Agents
- 🔍 **BM25 full-text search** — SQLite FTS5 with snippets and ranking — no embedding pipeline, no vector DB to run
- 🪝 **Zero-glue for Claude Code** — hooks auto-record sessions; MCP tools auto-recall on `SessionStart`
- 🪄 **Agent-initiated writes** — agents call `remember()` to persist what matters, not just passively recall
- 🧰 **Five MCP tools out of the box** — `search_memory`, `find_error`, `get_context`, `get_recent`, `remember`
- 🐍 **Python client for any agent** — `pip install bmfote-client` — same recall + write surface from the Messages API or the Agent SDK
- 🔒 **Self-hosted, bring-your-own-bearer** — your Turso, your token, your data

---

## How It Works

1. **Turso (libSQL) database** — your memory lives in a cloud-replicated SQLite, fully under your control
2. **FastAPI server** — 12 REST endpoints for search, sync, and stats
3. **MCP server** — 5 tools Claude Code calls automatically during a session
4. **Session hooks** — `SessionStart` / `PostToolUse` / `SessionEnd` stream every turn into the DB
5. **Python client** — `bmfote-client` gives non-Claude-Code agents the same recall + write surface

See [`CLAUDE.md`](CLAUDE.md) for architecture details.

---

## Quick Start

> **Prerequisites**
> - A running bmfote server (URL + `API_TOKEN`). Don't have one? See [Host your own server](#host-your-own-server) — ~5 minutes.
> - [Claude Code](https://claude.com/claude-code) installed on this machine.

Connect a machine to an existing bmfote deployment with one command:

```bash
npx bmfote setup --url https://your-bmfote-server --token <API_TOKEN>
```

Restart Claude Code. Context from previous sessions will automatically appear in new ones, and every new session will be saved back. Safe to re-run; run once per machine.

This command:
- Registers an MCP server (`bmfote-memory`) that exposes 5 memory tools
- Installs hooks at `~/.claude/hooks/bmfote-*.sh` for automatic session sync
- Writes `~/.claude/bmfote.env` with the URL and token
- Merges hook entries into `~/.claude/settings.json`

---

## Use from any agent SDK

bmfote isn't Claude-Code-only. If your agent runs on the Messages API, the Claude Agent SDK, or Anthropic Managed Agents, install the Python client and you get the same recall + write surface with no code specific to Claude Code.

```bash
pip install -e ./client
export BMFOTE_URL=https://your-bmfote-server
export BMFOTE_TOKEN=...
```

### Messages API — the full loop

Day 1 and Day 2 of an agent that continues its own research:

```python
import anthropic
from bmfote_client import Client, record_exchange

bmfote = Client()
session = bmfote.session(project="research-agent")

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

Or let the agent choose when to recall by exposing bmfote as **tools it can call mid-turn**:

```python
from bmfote_client import TOOL_SPECS, handle_tool_use

response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    tools=TOOL_SPECS,    # search_memory, find_error, get_context, get_recent, remember
    messages=[{"role": "user", "content": "What did we decide about Acme last week?"}],
)
# Handle any tool_use blocks with handle_tool_use(block, client=bmfote)
```

### Claude Agent SDK — no glue code

Plug the bmfote MCP server into options for reads, register hooks for writes, done:

```python
from claude_agent_sdk import ClaudeAgentOptions, query
from bmfote_client import agent_sdk_hooks

options = ClaudeAgentOptions(
    mcp_servers={
        "bmfote": {
            "type": "http",
            "url": "https://your-bmfote-server/mcp/",
            "headers": {"Authorization": f"Bearer {BMFOTE_TOKEN}"},
        }
    },
    hooks=agent_sdk_hooks(project="ops-agent"),
)
async for msg in query(prompt="Continue yesterday's investigation", options=options):
    ...
```

The agent gets `search_memory`, `find_error`, `get_context`, `get_recent`, and `remember` as tools automatically, and every user prompt + tool call is recorded back.

### Anthropic Managed Agents

Managed Agents don't expose client-side hooks, so the integration flips: the agent itself calls `remember` and `search_memory` as MCP tools against bmfote. bmfote ships a `bmfote-agent` CLI that handles the whole wiring — vault + credential, environment with `allowed_hosts`, agent config with `mcp_servers` + `mcp_toolset` + `always_allow` — in one command.

```bash
# Create a memory-only agent wired to bmfote (idempotent — reruns are no-ops)
bmfote-agent create \
  --name "my agent" \
  --system "You are a memory retrieval agent backed by bmfote."

# Run it with a prompt; returns the final agent response
bmfote-agent run <agent_id> "What did we decide about Acme last week?"

# Audit or retrofit an agent created elsewhere
bmfote-agent doctor <agent_id> --fix
bmfote-agent list
```

The CLI reads `BMFOTE_URL`, `BMFOTE_TOKEN` (from `npx bmfote setup`), and `ANTHROPIC_API_KEY` from your shell. Shared resources — a `bmfote-default` vault and `bmfote-default-env` environment — are discovered by name and created on first use, so there is no separate setup step.

All paths write into the same `messages` table as Claude Code sessions. See [`client/README.md`](client/README.md) for the full surface, failure semantics, and limitations.

---

## n8n + Managed Agents — cross-agent memory demo

Use n8n as a visual orchestration layer, Anthropic Managed Agents as the runtime, and bmfote as the shared memory substrate. No SDK, no deployment glue — just HTTP Request nodes. Any agent you invoke inherits the same memory pool, so different agent identities can read each other's work.

### The workflow

Ten n8n nodes — `HTTP Request` + one `IF` + one `Wait` + one `Code`:

```
Manual Trigger
  → Create Anthropic session         POST /v1/sessions
  → Send user message                POST /v1/sessions/{id}/events
  → Stash session id                 (Set)
  → Wait 3s  ←──┐
  → Poll events                      GET  /v1/sessions/{id}/events
  → IF last == session.status_idle   ├─ false → back to Wait
                                     └─ true  → Extract final answer (Code)
  → Create bmfote session            POST /api/sessions
  → Persist user message             POST /api/messages
  → Persist agent message            POST /api/messages
```

Every request to Anthropic sends:

```
x-api-key:         <ANTHROPIC_API_KEY>
anthropic-version: 2023-06-01
anthropic-beta:    managed-agents-2026-04-01
```

Every request to bmfote sends `Authorization: Bearer <BMFOTE_TOKEN>`. The `Create Session` body references an `agent_id` you previously created with `bmfote-agent create`, plus the shared `env_id` and `vault_id` the CLI auto-provisions.

### Why it matters

The agent invoked from n8n already has bmfote wired as an MCP server with `always_allow` permission (courtesy of `bmfote-agent create`). When the session runs, the agent calls `search_memory`, `get_context`, and `remember` mid-turn against bmfote — all orchestrated by Anthropic's infrastructure, not your machine. n8n never needs to know bmfote exists; it only sees the final answer. The persist-back nodes then write the Q/A pair into the same `messages` table Claude Code uses, so the next run — **from any agent on any surface** — can find it.

### Proven: cross-agent memory portability

Three sessions in project `n8n-managed-agent`, two different agent identities, one shared store:

| # | Agent | Prompt | Behavior |
|---|---|---|---|
| 1 | Agent A | *"What do you remember about the bmfote project?"* | Retrieved context, answered, persisted turn. |
| 2 | Agent A | *"What did you tell me last time?"* | Called `get_context(uuid=...-agent)` on Run 1's UUID, summarized it back. |
| 3 | **Fresh Agent B** (zero prior sessions) | *"Summarize every previous run and cite session IDs."* | Made 15 MCP calls including direct UUID lookups of both Run 1 and Run 2, reconstructed the full history, attributed it to Agent A, even flagged garbage rows from an earlier debug session as "known artifacts." |

A brand-new agent identity with no prior history correctly surfaced another agent's work. The substrate is the store, not the agent — memory is portable across agent identities.

### Prerequisites

- A bmfote server reachable from Anthropic's infrastructure (not `localhost`) — Anthropic's servers call the MCP endpoint, not your laptop.
- An `agent_id` from `bmfote-agent create`. The CLI reuses `bmfote-default` vault and env for every agent.
- `ANTHROPIC_API_KEY`, `BMFOTE_URL`, `BMFOTE_TOKEN` exposed to n8n as credentials.
- n8n running anywhere — self-hosted, n8n Cloud, or Docker.

---

## Host your own server

bmfote is self-hosted. You need a Turso database and any Docker-compatible host (Railway, Fly, Render, bare Docker). ~5 minutes end-to-end.

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
turso db create bmfote-memory
turso db show bmfote-memory --url              # -> libsql://...
turso db tokens create bmfote-memory --expiration none
```

Save the URL and token. You'll pass them to the server as environment variables.

### Step 3 — Apply the schema and generate an API token

```bash
turso db shell bmfote-memory < engine/schema.sql
openssl rand -hex 32    # save this — every client needs it
```

### Step 4 — Deploy the server

The server is a single `Dockerfile`. Pick your provider.

> **All commands below must be run from inside the cloned `bmfote` directory** (same shell as Step 1). Your provider CLI needs to see the `Dockerfile`.

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
docker build -t bmfote .
docker run -d -p 8000:8000 \
  -e TURSO_DATABASE_URL=libsql://... \
  -e TURSO_AUTH_TOKEN=... \
  -e API_TOKEN=... \
  bmfote
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

- **`railway up` says `Dockerfile not found` or `no build context`** — you're not inside the cloned `bmfote` directory. `cd` into it and retry.
- **`railway up` / `railway variables` says `No service linked` or `No services found`** — run `railway service`, pick or create a service, then re-run the failing command.
- **`fly launch` offers to generate a Dockerfile** — decline. The repo already ships one; make sure you ran `fly launch` from inside `bmfote/`.
- **`turso db shell` errors on `engine/schema.sql: No such file`** — you're not in the repo root. `cd bmfote` and retry.
- **`curl /health` returns connection refused** — the container failed to start. Check provider logs; the most common cause is a missing `API_TOKEN`, which makes the server fail closed.
- **`curl /api/stats` returns 401** — the `API_TOKEN` on the server does not match the token in your `Authorization: Bearer ...` header.
- **`/api/stats` returns zeros or empty** — schema was not applied. Re-run `turso db shell bmfote-memory < engine/schema.sql` from the repo root.

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

bmfote uses a split license:

- **Server, hooks, installer, and CLI** — [GNU AGPL-3.0](LICENSE). If you modify bmfote and run it as a network service, AGPL-3.0 requires you to make your modified source available to your users.
- **Python client library** ([`client/`](client/)) — [MIT](client/LICENSE). Free to embed in proprietary agent code with no copyleft obligations.

The server is AGPL so commercial re-hosters can't take bmfote, add private features, and compete as a closed SaaS. The client is MIT so you can drop it into any agent codebase — proprietary or not — without license friction.

---

**Built with FastMCP** | **Powered by Turso (libSQL)** | **AGPL-3.0 + MIT (split)**
