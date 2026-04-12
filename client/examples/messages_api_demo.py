"""Minimal demo — raw Anthropic Messages API loop writing to bmfote.

Run:
    pip install -e ./client anthropic
    export BMFOTE_URL=http://localhost:8026        # or your deployed URL
    export BMFOTE_TOKEN=...                         # if server has API_TOKEN set
    export ANTHROPIC_API_KEY=...
    python client/examples/messages_api_demo.py
"""

import os

import anthropic

from bmfote_client import Client, record_exchange


def main():
    user_prompt = "In one sentence, what is experiential memory?"

    anthropic_client = anthropic.Anthropic()
    response = anthropic_client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        max_tokens=512,
        messages=[{"role": "user", "content": user_prompt}],
    )

    bmfote = Client()
    session = bmfote.session(project="bmfote-client-smoke")
    record_exchange(session, user_prompt, response)
    session.close()

    print(f"session_id={session.session_id}")
    print(f"first assistant text: {response.content[0].text[:120]}")


if __name__ == "__main__":
    main()
