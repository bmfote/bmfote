# bmfote-client

Drop-in Python client for **reading and writing** agent turns against a [bmfote](../README.md) memory server. An agent that uses both sides gets full cloud context — it recalls what prior sessions did, and deposits its own work for the next session to find.

If you're running agents *inside* Claude Code, you don't need the write side — the existing hooks already sync your sessions. You still might want the read side if you want your Claude Code session to pull bmfote context into a prompt manually. This package is primarily for agents built on the **Anthropic Messages API** or the **Claude Agent SDK**, which have no auto-sync path today.

## Install

```bash
pip install -e ./client           # from the bmfote repo
# or, once published:
# pip install bmfote-client
```

Optional extras: `pip install "bmfote-client[anthropic]"` or `"bmfote-client[agent-sdk]"` (just pulls in the respective SDK; the client itself duck-types both).

## Configure

The client reads two env vars:

```bash
export BMFOTE_URL=https://your-bmfote-host
export BMFOTE_TOKEN=...            # same API_TOKEN the server was deployed with
```

Or pass them explicitly to `Client(url=..., token=...)`.

## Usage — Anthropic Messages API

```python
import anthropic
from bmfote_client import Client, record_exchange

anthropic_client = anthropic.Anthropic()
bmfote = Client()
session = bmfote.session(project="research-agent")

user_prompt = "Summarize the key risks in our Q3 plan."
response = anthropic_client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": user_prompt}],
)

record_exchange(session, user_prompt, response)
session.close()
```

`record_exchange` flattens each content block the same way `engine/sync_conversations.py` does for Claude Code sessions, so your API-agent turns land in the database indistinguishable from interactive ones.

## Reading — the full loop

The point of cloud context is that an agent launched two days later can recall what the previous session did. There are two ways to pull that context back into a Messages API loop.

### Pattern A — recall into the system prompt (simplest)

```python
import anthropic
from bmfote_client import Client, record_exchange

bmfote = Client()
session = bmfote.session(project="research-agent")

# Pull prior memory and stuff it into the system prompt
context = session.recall("competitor pricing research", limit=10)

ac = anthropic.Anthropic()
response = ac.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    system=f"You are a research agent.\n\n{context}",
    messages=[{"role": "user", "content": "Continue from where we left off."}],
)

record_exchange(session, "Continue from where we left off.", response)
session.close()
```

`Session.recall(query, limit=10)` returns a pre-formatted multi-line string ready to drop into the `system=` field. Empty results return a short sentinel sentence. No errors on network failure — returns the sentinel.

### Pattern B — let the agent search on its own (tool use)

Expose bmfote as a set of tools the model can call mid-turn. Four tools are provided, mirroring the MCP server: `search_memory`, `find_error`, `get_context`, `get_recent`.

```python
import anthropic
from bmfote_client import Client, TOOL_SPECS, handle_tool_use, record_exchange

bmfote = Client()
session = bmfote.session(project="research-agent")
ac = anthropic.Anthropic()

messages = [{"role": "user", "content": "What did we decide about Acme's pricing last week?"}]

while True:
    response = ac.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        tools=TOOL_SPECS,
        messages=messages,
    )
    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason != "tool_use":
        break

    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            result_str = handle_tool_use(block, client=bmfote)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })
    messages.append({"role": "user", "content": tool_results})

record_exchange(session, messages[0]["content"], response)
session.close()
```

The agent can now *decide* when to recall — if the prompt is small it may skip search entirely; if it's ambiguous it may call `search_memory` twice with different terms. That's the experiential-memory pattern in full: the agent consults its own history on demand.

### Direct read methods (if you just want the data)

`Client` exposes the same four endpoints as plain Python calls — useful for scripting, dashboards, or wiring bmfote into a non-Anthropic loop:

```python
client.search("query", limit=10)                   # → list[dict]
client.find_error("connection refused", limit=5)   # → list[dict]
client.recent(hours=24, limit=50)                  # → list[dict]
client.get_message(uuid, context=1)                # → dict | None
```

All read methods fail silent on network error (return `[]` or `None` and log a warning).

## Usage — Claude Agent SDK

```python
import asyncio
from claude_agent_sdk import ClaudeAgentOptions, query
from bmfote_client import agent_sdk_hooks

async def main():
    options = ClaudeAgentOptions(
        tools=["bash"],
        hooks=agent_sdk_hooks(project="ops-agent"),
    )
    async for msg in query(prompt="Check disk usage on /var", options=options):
        print(msg)

asyncio.run(main())
```

`agent_sdk_hooks` registers callbacks for `UserPromptSubmit`, `PostToolUse`, and `Stop`. User prompts and tool calls (with truncated results) are recorded automatically.

For the **read side** on Agent SDK, wire the bmfote MCP server into `ClaudeAgentOptions.mcp_servers` and the agent gets `search_memory` / `find_error` / `get_context` / `get_recent` / `remember` as tools automatically — no extra code:

```python
options = ClaudeAgentOptions(
    mcp_servers={
        "bmfote": {
            "type": "http",
            "url": "https://your-bmfote-host/mcp/",
            "headers": {"Authorization": f"Bearer {BMFOTE_TOKEN}"},
        }
    },
    hooks=agent_sdk_hooks(project="ops-agent"),
)
```

That's the full two-direction loop for Agent SDK users: read via MCP tools, write via hooks, zero glue code.

## Usage — Anthropic Managed Agents

Managed Agents don't expose client-side lifecycle hooks the way the Agent SDK does, so the integration pattern is different: the agent writes to bmfote by **calling tools itself** (via the `remember` MCP tool), not through hooks the orchestrator registers. The agent and orchestrator roles are cleanly separated:

- **Orchestrator (your Python code, n8n, whatever):** creates the session, sends the user message, streams/polls for completion. Does **not** need to pre-fetch context or post-write results.
- **Agent (hosted at Anthropic):** calls `search_memory` / `find_error` / etc. on its own when it needs to recall, and calls `remember` when it finishes something worth persisting.

### One-time setup

```python
from anthropic import Anthropic
client = Anthropic()

# 1. Store BMFOTE_TOKEN in a vault so the agent can reach bmfote with auth
vault = client.beta.vaults.create(
    name="bmfote-credentials",
    credentials=[{
        "type": "static_bearer",
        "mcp_server_url": "https://your-bmfote-host/mcp/",
        "token": os.environ["BMFOTE_TOKEN"],
    }],
    betas=["managed-agents-2026-04-01"],
)

# 2. Update the agent to add bmfote as an MCP server + enable mcp_toolset
client.beta.agents.update(
    agent_id="agent_011CZy...",
    mcp_servers=[{
        "type": "url",
        "name": "bmfote",
        "url": "https://your-bmfote-host/mcp/",
    }],
    tools=[
        {"type": "agent_toolset_20260401"},     # built-in bash/read/write/etc.
        {"type": "mcp_toolset"},                # exposes bmfote tools to the agent
    ],
    betas=["managed-agents-2026-04-01"],
)
```

### Per research task

```python
session = client.beta.sessions.create(
    agent="agent_011CZy...",
    environment_id="env_...",
    vault_ids=[vault.id],                       # binds the bmfote bearer
    betas=["managed-agents-2026-04-01"],
)

client.beta.sessions.events.send(
    session_id=session.id,
    events=[{
        "type": "user.message",
        "content": [{"type": "text", "text": "Research the VP of Sales at Best Buy and save what you find."}],
    }],
    betas=["managed-agents-2026-04-01"],
)

with client.beta.sessions.events.stream(session.id, betas=["managed-agents-2026-04-01"]) as stream:
    for event in stream:
        if event.type == "agent.message":
            print(event.content[0].text)
        elif event.type == "session.status_idle":
            break
```

No pre-fetch, no post-write. The agent handles recall and persistence itself through the MCP tools. Day 3's session calls `search_memory("Best Buy VP sales")` on its own and finds what Day 1's session saved via `remember`.

### Tool surface exposed to the agent

Once the MCP server is wired up, the agent sees **5 tools** from bmfote:

| Tool | Direction | Use |
|---|---|---|
| `search_memory` | read | Full-text search over all prior conversation messages |
| `find_error` | read | Find past errors and the response that followed |
| `get_context` | read | Expand a search hit into its surrounding conversation |
| `get_recent` | read | What was recently worked on? |
| `remember` | **write** | Save a finding / decision / fact for future sessions to recall |

The `remember` tool writes into the same `messages` table `search_memory` reads from, so there's no split between "what I can write" and "what I can read" — it's one homogeneous memory store.

## What gets stored

Every write lands in the same `messages` table the Claude Code sync writes to:

| field | source |
|---|---|
| `session_id` | yours, or auto-generated uuid4 |
| `uuid` | auto-generated per write |
| `parent_uuid` | chained to the previous write in this session |
| `type` | `"user"` or `"assistant"` |
| `content` | flattened text, capped at 50,000 chars |
| `model` | `response.model` (Messages API only) |
| `input_tokens` / `output_tokens` | `response.usage` (Messages API only) |
| `project` | whatever you pass to `session(project=...)` |

## Failure behavior

Writes use a sync POST with a **2-second timeout and fail silent**. If bmfote is down or the network is flaky, the client logs a `WARNING` via the `bmfote_client` logger and the agent keeps running. A dropped turn is lost, not retried. This is intentional for v1 — reliability upgrades (background queue + local spool) can land in v2 without breaking this API.

## Limitations

- **No assistant-text capture from Agent SDK hooks.** The SDK exposes hooks for user prompts, tool use, and stop — but not for plain assistant text between tool calls. If you need those too, tail the JSONL transcript at `input_data["transcript_path"]` (same format `engine/sync_conversations.py` already handles).
- **Single-tenant.** Everyone with the same `API_TOKEN` writes to the same memory pool. Segment by setting distinct `project` names.
- **Content cap: 50k chars.** Long tool outputs get truncated client-side to match the server's `MessageCreate` limit. Tool results are further truncated to 500 chars to match the existing Claude Code sync convention.
