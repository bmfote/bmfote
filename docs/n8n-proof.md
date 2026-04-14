# Proof: one memory, multiple agent identities

The cross-surface story only matters if it actually works across *different agents*, not just the same agent on different days. This is the full reproduction, live against the real APIs. It runs on n8n + Anthropic Managed Agents + bmfote, with no custom SDK code — just HTTP Request nodes.

If you're looking for the short version, it's this:

> **A brand-new agent identity (Agent B) with zero prior sessions correctly surfaced the work of a different agent identity (Agent A) via MCP calls against bmfote, reconstructing a full history it had never seen before and even flagging garbage rows from an earlier debug session as "known artifacts." The substrate is the store, not the agent.**

Everything below is the reproduction.

## The three-run proof

Three sessions in project `n8n-managed-agent`, **two different agent identities, one shared store**:

| # | Agent | Prompt | Behavior |
|---|---|---|---|
| 1 | Agent A | *"What do you remember about the bmfote project?"* | Retrieved context, answered, persisted turn. |
| 2 | Agent A | *"What did you tell me last time?"* | Called `get_context(uuid=...-agent)` on Run 1's UUID, summarized it back. |
| 3 | **Fresh Agent B** (zero prior sessions) | *"Summarize every previous run and cite session IDs."* | Made 15 MCP calls including direct UUID lookups of both Run 1 and Run 2, reconstructed the full history, attributed it to Agent A, even flagged garbage rows from an earlier debug session as "known artifacts." |

Memory is portable across agent identities, not tied to any one of them.

## The workflow that produced it

Use n8n as a visual orchestration layer, Anthropic Managed Agents as the runtime, bmfote as the shared memory substrate. No SDK, no deployment glue — just HTTP Request nodes.

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

## Why it matters

The agent invoked from n8n already has bmfote wired as an MCP server with `always_allow` permission (courtesy of `bmfote-agent create`). When the session runs, the agent calls `search_memory`, `get_context`, and `remember` mid-turn against bmfote — all orchestrated by Anthropic's infrastructure, not your machine. n8n never needs to know bmfote exists; it only sees the final answer. The persist-back nodes then write the Q/A pair into the same `messages` table Claude Code uses, so the next run — **from any agent on any surface** — can find it.

## Prerequisites

- A bmfote server reachable from Anthropic's infrastructure (not `localhost`) — Anthropic's servers call the MCP endpoint, not your laptop.
- An `agent_id` from `bmfote-agent create`. The CLI reuses `bmfote-default` vault and env for every agent.
- `ANTHROPIC_API_KEY`, `BMFOTE_URL`, `BMFOTE_TOKEN` exposed to n8n as credentials.
- n8n running anywhere — self-hosted, n8n Cloud, or Docker.

## Original commit

The three-run proof was captured in commit [`438d91b`](https://github.com/bmfote/bmfote/commit/438d91b) — *"Document n8n + Managed Agents cross-agent memory demo"*.
