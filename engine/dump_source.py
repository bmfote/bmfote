#!/usr/bin/env python3
"""Dump source SQLite data as INSERT statements for piping to turso db shell."""

import json
import sqlite3
import sys
from pathlib import Path

SOURCE_DB = Path.home() / "claude-conversations-db" / "conversations.db"


def quote(val):
    """SQL-escape a value for INSERT statements."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).replace("'", "''")
    return f"'{s}'"


def dump_table(conn, table, columns):
    """Generate INSERT statements for a table."""
    col_list = ", ".join(columns)
    rows = conn.execute(f"SELECT {col_list} FROM {table}").fetchall()
    count = 0
    for row in rows:
        values = ", ".join(quote(v) for v in row)
        print(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({values});")
        count += 1
    print(f"-- {table}: {count} rows", file=sys.stderr)
    return count


def main():
    conn = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)

    print("BEGIN;")

    dump_table(conn, "sessions", [
        "session_id", "project", "first_message_at", "last_message_at", "message_count"
    ])

    dump_table(conn, "messages", [
        "uuid", "session_id", "parent_uuid", "type", "role", "content",
        "model", "input_tokens", "output_tokens", "timestamp"
    ])

    dump_table(conn, "vault_docs", [
        "file_path", "project", "topic", "date", "outcome", "tags",
        "doc_type", "content", "frontmatter_json", "last_modified", "checksum"
    ])

    print("COMMIT;")

    conn.close()


if __name__ == "__main__":
    main()
