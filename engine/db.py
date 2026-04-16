"""Shared database layer — Turso Cloud (libSQL embedded replica)."""

import os
from pathlib import Path
from typing import Optional

import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
DB_PATH = Path(__file__).parent / "local-replica.db"


def is_remote_db() -> bool:
    """True in cloud/Docker mode (direct libSQL), False for local dev (embedded replica).

    RAILWAY_ENVIRONMENT is kept as a backward-compat fallback so existing Railway
    deployments keep working during the transition to CCTX_REMOTE_DB.
    """
    return bool(os.getenv("CCTX_REMOTE_DB") or os.getenv("RAILWAY_ENVIRONMENT"))


def get_connection():
    """Create libSQL connection. Embedded replica for local dev, remote in cloud mode."""
    if is_remote_db():
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        return libsql.connect(
            database=str(DB_PATH),
            sync_url=TURSO_URL,
            auth_token=TURSO_TOKEN,
        )


# Module-level singleton — reused across requests
_conn = None


def _open_and_heal():
    """Open a libSQL connection, sync, and drain any stuck WAL frames.

    A `PRAGMA wal_checkpoint(TRUNCATE)` on a freshly-opened connection
    clears any frames left behind by a previous writer that died mid-sync
    (historically, a cron script sharing the same replica file). Harmless
    when the WAL is already clean — returns (0, 0, 0) in that case.
    """
    conn = get_connection()
    if not is_remote_db():
        conn.sync()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except Exception:
            # Non-fatal: the checkpoint is a nice-to-have, not a hard
            # requirement. If it fails, writes will surface the real error.
            pass
    return conn


def get_conn():
    """Get or create the shared database connection. Resets on failure."""
    global _conn
    if _conn is None:
        _conn = _open_and_heal()
    try:
        _conn.execute("SELECT 1")
    except Exception:
        _conn = _open_and_heal()
    return _conn


def rows_to_dicts(cursor) -> list[dict]:
    """Convert libSQL cursor results to list of dicts."""
    if cursor.description is None:
        return []
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def row_to_dict(cursor) -> Optional[dict]:
    """Fetch one row as dict, or None."""
    if cursor.description is None:
        return None
    columns = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))
