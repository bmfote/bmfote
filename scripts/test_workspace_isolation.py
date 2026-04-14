#!/usr/bin/env python3
"""Verify workspace_id isolation end-to-end against the local replica.

Scenarios exercised:
  1. Existing data is readable via default workspace (backwards compat).
  2. FTS search still ranks results as before.
  3. A message written to a custom workspace is NOT visible from the default.
  4. The same message IS visible from its own workspace.
  5. query_message (UUID lookup) respects workspace isolation even when the
     UUID is known to a different workspace.
  6. query_recent respects workspace isolation.
  7. Cleanup: test rows removed, nothing leaks.

Run:
    python -m scripts.test_workspace_isolation
"""

import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.db import get_conn, is_remote_db  # noqa: E402
from engine.server import (  # noqa: E402
    DEFAULT_WORKSPACE,
    query_search,
    query_message,
    query_recent,
)


TEST_WORKSPACE = "test-isolation-xyz"
TEST_SESSION = "test-isolation-session-xyz"
SENTINEL = "BMFOTE_WORKSPACE_ISOLATION_SENTINEL_PHRASE"


def section(label: str) -> None:
    print(f"\n=== {label} ===")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[ok]   {msg}")


def cleanup(conn) -> None:
    conn.execute(
        "DELETE FROM messages WHERE workspace_id = ? OR session_id = ?",
        (TEST_WORKSPACE, TEST_SESSION),
    )
    conn.execute(
        "DELETE FROM sessions WHERE session_id = ?",
        (TEST_SESSION,),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()


def main() -> int:
    conn = get_conn()
    mode = "remote" if is_remote_db() else "local replica"
    print(f"bmfote workspace isolation test ({mode})")
    print(f"default workspace: {DEFAULT_WORKSPACE!r}")
    print(f"test workspace:    {TEST_WORKSPACE!r}")

    # Pre-flight: clean up any leftover state from a prior failed run
    cleanup(conn)

    # --- 1. schema check
    section("1. schema check")
    has_col = conn.execute(
        "SELECT 1 FROM pragma_table_info('messages') WHERE name = 'workspace_id'"
    ).fetchone()
    if has_col is None:
        fail("messages.workspace_id column not found — run scripts/migrate_workspace_id.py first")
    ok("messages.workspace_id column present")

    has_idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_messages_workspace'"
    ).fetchone()
    if has_idx is None:
        fail("idx_messages_workspace not found")
    ok("idx_messages_workspace present")

    # --- 2. existing data still readable under default workspace
    section("2. backwards compat — existing data readable")
    total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    default_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE workspace_id = ?",
        (DEFAULT_WORKSPACE,),
    ).fetchone()[0]
    print(f"total messages:            {total_msgs}")
    print(f"messages in default ws:    {default_msgs}")
    if default_msgs == 0:
        fail("no messages in default workspace — migration may not have backfilled")
    if default_msgs != total_msgs:
        print(
            f"[warn] {total_msgs - default_msgs} messages in non-default workspace already "
            f"(probably leftover test rows; continuing)"
        )
    else:
        ok("all pre-existing messages backfilled to default workspace")

    # --- 3. FTS still works for default workspace
    section("3. FTS still works for default workspace")
    # Use a high-frequency, near-guaranteed token in prod data
    for probe in ("the", "and", "error", "bmfote"):
        results = query_search(probe, limit=3)
        if results:
            ok(f"FTS hit for {probe!r}: {len(results)} results")
            break
    else:
        print("[warn] no FTS hits for common tokens; data may be sparse locally")

    # --- 4. write a sentinel message to the test workspace
    section("4. isolation — sentinel write to test workspace")
    conn.execute(
        """
        INSERT INTO sessions (session_id, project, first_message_at, last_message_at, message_count)
        VALUES (?, 'isolation-test', ?, ?, 1)
        ON CONFLICT(session_id) DO NOTHING
        """,
        (
            TEST_SESSION,
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    test_uuid = str(_uuid.uuid4())
    conn.execute(
        """
        INSERT INTO messages (uuid, session_id, type, role, content, timestamp, workspace_id)
        VALUES (?, ?, 'assistant', 'assistant', ?, ?, ?)
        """,
        (
            test_uuid,
            TEST_SESSION,
            SENTINEL,
            datetime.now(timezone.utc).isoformat(),
            TEST_WORKSPACE,
        ),
    )
    conn.commit()
    if not is_remote_db():
        conn.sync()
    ok(f"inserted sentinel message uuid={test_uuid} in workspace={TEST_WORKSPACE!r}")

    # --- 5. sentinel is INVISIBLE from the default workspace via search
    section("5. sentinel is invisible from default workspace")
    default_hits = query_search(SENTINEL, limit=10)
    if default_hits:
        fail(
            f"LEAK: sentinel visible in default workspace via query_search "
            f"({len(default_hits)} results)"
        )
    ok("query_search(default) does not see the sentinel")

    # --- 6. sentinel is VISIBLE from the test workspace via search
    section("6. sentinel is visible from its own workspace")
    test_hits = query_search(SENTINEL, limit=10, workspace_id=TEST_WORKSPACE)
    if not test_hits:
        fail(f"sentinel NOT visible in its own workspace {TEST_WORKSPACE!r}")
    if not any(h["uuid"] == test_uuid for h in test_hits):
        fail(f"sentinel hits returned but not our uuid {test_uuid}")
    ok(f"query_search({TEST_WORKSPACE!r}) found sentinel (1+ hit)")

    # --- 7. UUID lookup respects workspace isolation
    section("7. query_message respects workspace isolation")
    msg_from_default = query_message(test_uuid, context=0)
    if msg_from_default is not None:
        fail(f"LEAK: query_message(default) returned a message belonging to {TEST_WORKSPACE!r}")
    ok("query_message(default) returns None for foreign UUID")

    msg_from_test = query_message(test_uuid, context=0, workspace_id=TEST_WORKSPACE)
    if msg_from_test is None:
        fail("query_message(test workspace) returned None for its own UUID")
    if msg_from_test.get("uuid") != test_uuid:
        fail("query_message returned wrong uuid")
    ok("query_message(test workspace) returns the correct message")

    # --- 8. query_recent respects workspace isolation
    section("8. query_recent respects workspace isolation")
    recent_default = query_recent(hours=1, limit=100)
    if any(m["uuid"] == test_uuid for m in recent_default):
        fail("LEAK: query_recent(default) returned a message from the test workspace")
    ok("query_recent(default) does not include sentinel")

    recent_test = query_recent(hours=1, limit=100, workspace_id=TEST_WORKSPACE)
    if not any(m["uuid"] == test_uuid for m in recent_test):
        fail("query_recent(test workspace) did not return sentinel")
    ok("query_recent(test workspace) includes sentinel")

    # --- 9. cleanup
    section("9. cleanup")
    cleanup(conn)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE workspace_id = ?",
        (TEST_WORKSPACE,),
    ).fetchone()[0]
    if remaining != 0:
        fail(f"cleanup incomplete: {remaining} rows still in test workspace")
    ok("test rows removed")

    # --- 10. post-cleanup sanity
    section("10. post-cleanup sanity")
    final_total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if final_total != total_msgs:
        fail(
            f"final message count {final_total} != pre-test total {total_msgs} "
            f"(leaked or dropped rows)"
        )
    ok(f"message count unchanged: {final_total}")

    print("\n[PASS] workspace_id isolation verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
