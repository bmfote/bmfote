"""Minimal demo — Claude Agent SDK run with bmfote hooks.

Run:
    pip install -e ./client claude-agent-sdk
    export BMFOTE_URL=http://localhost:8026
    export BMFOTE_TOKEN=...                # if server has API_TOKEN set
    export ANTHROPIC_API_KEY=...
    python client/examples/agent_sdk_demo.py
"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query

from bmfote_client import agent_sdk_hooks


async def main():
    options = ClaudeAgentOptions(
        tools=["bash"],
        hooks=agent_sdk_hooks(project="bmfote-client-smoke"),
        max_turns=3,
    )
    async for message in query(
        prompt="Run `date` and report the current time.",
        options=options,
    ):
        print(type(message).__name__)


if __name__ == "__main__":
    asyncio.run(main())
