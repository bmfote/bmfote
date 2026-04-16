#!/usr/bin/env python3
"""Add workspace_id column to messages table. Idempotent. Run once per environment.

Usage:
    python -m scripts.migrate_workspace_id

Safe to run multiple times. Checks for the column before altering.
In local-dev mode, syncs the change up to Turso Cloud after the ALTER.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.db import get_conn, is_remote_db  # noqa: E402


def column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ?",
        (table, column),
    ).fetchone()
    return row is not None


def main() -> int:
    conn = get_conn()
    mode = "remote (Turso direct)" if is_remote_db() else "local (embedded replica)"
    print(f"[migrate] mode: {mode}")

    if column_exists(conn, "messages", "workspace_id"):
        print("[skip] messages.workspace_id already exists")
        return 0

    print("[migrate] adding messages.workspace_id ...")
    conn.execute(
        "ALTER TABLE messages ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'cctx-default'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace_id)"
    )
    conn.commit()

    if not is_remote_db():
        print("[sync] pushing schema change to Turso Cloud ...")
        conn.sync()

    # Sanity check after the migration
    if not column_exists(conn, "messages", "workspace_id"):
        print("[fail] column not present after ALTER — investigate")
        return 1

    count_default = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE workspace_id = 'cctx-default'"
    ).fetchone()[0]
    print(f"[done] migration complete. {count_default} messages backfilled to 'cctx-default'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
