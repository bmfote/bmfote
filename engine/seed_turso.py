#!/usr/bin/env python3
"""One-time migration: copy all data from local SQLite to Turso via libsql.

Reads from ~/claude-conversations-db/conversations.db (READ-ONLY).
Writes to Turso embedded replica, then syncs to cloud.
Order: sessions → messages → vault_docs. Batched in groups of 500.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SOURCE_DB = Path.home() / "claude-conversations-db" / "conversations.db"
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
REPLICA_PATH = Path(__file__).parent / "local-replica.db"
BATCH_SIZE = 500


def get_source():
    """Open source database READ-ONLY."""
    conn = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_target():
    """Open Turso embedded replica."""
    conn = libsql.connect(
        database=str(REPLICA_PATH),
        sync_url=TURSO_URL,
        auth_token=TURSO_TOKEN,
    )
    conn.sync()
    return conn


def migrate_table(src, dst, table, columns, batch_size=BATCH_SIZE):
    """Migrate rows from source to target in batches."""
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    insert_sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"

    rows = src.execute(f"SELECT {col_list} FROM {table}").fetchall()
    total = len(rows)
    migrated = 0

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        for row in batch:
            dst.execute(insert_sql, tuple(row))
        dst.commit()
        migrated += len(batch)
        print(f"  {table}: {migrated}/{total}", end="\r")

    print(f"  {table}: {migrated}/{total} rows migrated")
    return migrated


def main():
    if not SOURCE_DB.exists():
        print(f"Source database not found: {SOURCE_DB}")
        sys.exit(1)

    print(f"Source: {SOURCE_DB}")
    print(f"Target: {TURSO_URL}")
    print(f"Replica: {REPLICA_PATH}")
    print()

    src = get_source()
    dst = get_target()

    # Check if target already has data
    existing = dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if existing > 0:
        print(f"Target already has {existing} messages.")
        print("To re-seed, clear the target first:")
        print("  turso db shell claude-memory 'DELETE FROM messages; DELETE FROM vault_docs; DELETE FROM sessions;'")
        sys.exit(1)

    t0 = time.time()

    # 1. Sessions
    migrate_table(src, dst, "sessions", [
        "session_id", "project", "first_message_at", "last_message_at", "message_count"
    ])

    # 2. Messages (largest table — batch 500)
    migrate_table(src, dst, "messages", [
        "uuid", "session_id", "parent_uuid", "type", "role", "content",
        "model", "input_tokens", "output_tokens", "timestamp"
    ])

    # 3. Vault docs
    migrate_table(src, dst, "vault_docs", [
        "file_path", "project", "topic", "date", "outcome", "tags",
        "doc_type", "content", "frontmatter_json", "last_modified", "checksum"
    ])

    elapsed = time.time() - t0
    print(f"\nLocal migration complete in {elapsed:.1f}s")

    # Sync to cloud
    print("Syncing to Turso Cloud...")
    t1 = time.time()
    dst.sync()
    print(f"Cloud sync complete in {time.time() - t1:.1f}s")

    # Verify counts
    print("\nVerification:")
    for table in ["sessions", "messages", "vault_docs"]:
        src_count = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dst_count = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        match = "OK" if src_count == dst_count else "MISMATCH"
        print(f"  {table}: source={src_count} target={dst_count} [{match}]")

    src.close()


if __name__ == "__main__":
    main()
