# bmfote

Cloud-synced experiential memory for AI agents, powered by Turso (libSQL).

Two parts: deploy a shared server once, then connect each machine.

---

## Part 1: Deploy the server (once per team)

You need a Turso database and any Docker-compatible host.

### Step 1 — Clone the repo

```bash
git clone https://github.com/bmfote/bmfote && cd bmfote
```

Keep this shell open. Every command in Part 1 runs from inside this directory.

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

---

## Part 2: Connect a machine

On each machine you want to connect to the same memory:

```bash
npx bmfote setup --url https://your-domain --token <API_TOKEN>
```

This configures Claude Code on the current machine:
- Registers an MCP server (`bmfote-memory`) that exposes 5 memory tools
- Installs hooks at `~/.claude/hooks/bmfote-*.sh` for automatic session sync
- Writes `~/.claude/bmfote.env` with the URL and token
- Merges hook entries into `~/.claude/settings.json`

Safe to re-run. Run once per machine.

---

## Part 3: Cloud context for agents outside Claude Code

If your agent runs on the raw Anthropic Messages API or the Claude Agent SDK — not inside Claude Code — use the [`bmfote-client`](client/README.md) Python package. It gives you **both directions**: the agent can recall what prior sessions did, and deposit its own work for the next session to find.

```bash
pip install -e ./client
export BMFOTE_URL=https://your-domain
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
    tools=TOOL_SPECS,    # search_memory, find_error, get_context, get_recent, search_vault
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
            "url": "https://your-domain/mcp/",
            "headers": {"Authorization": f"Bearer {BMFOTE_TOKEN}"},
        }
    },
    hooks=agent_sdk_hooks(project="ops-agent"),
)
async for msg in query(prompt="Continue yesterday's investigation", options=options):
    ...
```

The agent gets `search_memory`, `find_error`, `get_context`, `get_recent`, `search_vault`, and `remember` as tools automatically, and every user prompt + tool call is recorded back.

### Anthropic Managed Agents — agent-initiated reads and writes

Managed Agents don't expose client-side hooks, so the integration is flipped: the orchestrator just creates sessions and sends messages; the **agent itself** calls `remember` when it has something to persist and `search_memory` when it needs to recall. One-time setup wires the bmfote MCP server into the agent config and stores `BMFOTE_TOKEN` in an Anthropic vault:

```python
# One-time: store the bmfote bearer in a vault and attach bmfote to the agent
vault = client.beta.vaults.create(
    credentials=[{"type": "static_bearer",
                  "mcp_server_url": "https://your-domain/mcp/",
                  "token": BMFOTE_TOKEN}],
    betas=["managed-agents-2026-04-01"],
)
client.beta.agents.update(
    agent_id="agent_011CZy...",
    mcp_servers=[{"type": "url", "name": "bmfote", "url": "https://your-domain/mcp/"}],
    tools=[{"type": "agent_toolset_20260401"}, {"type": "mcp_toolset"}],
    betas=["managed-agents-2026-04-01"],
)

# Per run: just create a session, send a message, stream events
session = client.beta.sessions.create(
    agent="agent_011CZy...", environment_id="env_...",
    vault_ids=[vault.id], betas=["managed-agents-2026-04-01"],
)
client.beta.sessions.events.send(
    session_id=session.id,
    events=[{"type": "user.message", "content": [{"type": "text", "text": "Research X and save what you find."}]}],
    betas=["managed-agents-2026-04-01"],
)
```

No pre-fetch, no post-write. The agent handles recall and persistence itself through MCP tools. See [`client/README.md`](client/README.md#usage--anthropic-managed-agents) for the full pattern.

All paths write into the same `messages` table as Claude Code sessions. See [`client/README.md`](client/README.md) for the full surface, failure semantics, and limitations.

---

## Local development

```bash
source .venv/bin/activate      # Python 3.12
python -m engine.server        # starts on PORT from .env (default 8026)
```

Local dev uses an embedded libSQL replica at `engine/local-replica.db` that syncs
to your Turso database. Auth is optional locally (no `API_TOKEN` required).
