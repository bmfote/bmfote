#!/usr/bin/env python3
"""Incremental sync — process new JSONL messages straight into Turso Cloud.

Uses a direct Turso HTTP connection (no embedded replica) so this script
never contends with the server process for the local-replica.db WAL.
A crash here cannot poison the server's replica file.
"""

import json
import glob
import os
import sys
from pathlib import Path

import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")


# =============================================================
# JSONL parsing helpers (ported from build_db.py)
# =============================================================

def extract_content(message: dict) -> str:
    """Extract text content from a message, handling both string and block formats."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', 'unknown')}]")
                elif block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        parts.append(result_content[:500])
                    elif isinstance(result_content, list):
                        for sub in result_content:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                parts.append(sub.get("text", "")[:500])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def parse_timestamp(ts: str) -> str:
    """Normalize ISO timestamp."""
    if ts and ts.endswith("Z"):
        return ts[:-1] + "+00:00"
    return ts or ""


def derive_project(dirpath: str) -> str:
    """Derive project name from the JSONL directory path."""
    dirname = os.path.basename(dirpath)
    parts = dirname.split("-")
    for i, part in enumerate(parts):
        if part in ("projects", "dev") and i + 1 < len(parts):
            return "-".join(parts[i + 1:])
    return dirname


# =============================================================
# Sync
# =============================================================

def get_conn():
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)


def update():
    conn = get_conn()

    # Get the latest timestamp we've seen
    row = conn.execute("SELECT MAX(timestamp) FROM messages").fetchone()
    cutoff = row[0] if row and row[0] else ""
    print(f"Syncing messages after: {cutoff or '(none — run seed_turso.py first)'}")

    if not cutoff:
        print("No existing data. Run seed_turso.py first.")
        return

    new_messages = 0
    updated_sessions = set()

    jsonl_files = glob.glob(str(CLAUDE_PROJECTS / "*" / "*.jsonl"))

    for filepath in jsonl_files:
        dirpath = os.path.dirname(filepath)
        project = derive_project(dirpath)
        session_id = Path(filepath).stem

        with open(filepath) as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = record.get("type")
                if rec_type not in ("user", "assistant"):
                    continue

                timestamp = parse_timestamp(record.get("timestamp", ""))
                if not timestamp or timestamp <= cutoff:
                    continue

                uuid = record.get("uuid")
                if not uuid:
                    continue

                message = record.get("message", {})
                content = extract_content(message)
                if not content.strip():
                    continue

                usage = message.get("usage", {})

                conn.execute("""
                    INSERT OR IGNORE INTO messages
                    (uuid, session_id, parent_uuid, type, role, content,
                     model, input_tokens, output_tokens, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    uuid, session_id,
                    record.get("parentUuid"),
                    rec_type,
                    message.get("role", rec_type),
                    content,
                    message.get("model"),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    timestamp,
                ))
                new_messages += 1
                updated_sessions.add((session_id, project))

    # Update session metadata for affected sessions
    for session_id, project in updated_sessions:
        row = conn.execute("""
            SELECT MIN(timestamp), MAX(timestamp), COUNT(*)
            FROM messages WHERE session_id = ?
        """, (session_id,)).fetchone()

        if row:
            conn.execute("""
                INSERT OR REPLACE INTO sessions
                (session_id, project, first_message_at, last_message_at, message_count)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, project, row[0], row[1], row[2]))

    conn.commit()

    print(f"Synced {new_messages} new messages across {len(updated_sessions)} sessions")


if __name__ == "__main__":
    update()
