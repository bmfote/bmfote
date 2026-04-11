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


def get_connection():
    """Create libSQL connection. Embedded replica locally, remote on Railway."""
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        return libsql.connect(
            database=str(DB_PATH),
            sync_url=TURSO_URL,
            auth_token=TURSO_TOKEN,
        )


# Module-level singleton — reused across requests
_conn = None


def get_conn():
    """Get or create the shared database connection."""
    global _conn
    if _conn is None:
        _conn = get_connection()
        if not os.getenv("RAILWAY_ENVIRONMENT"):
            _conn.sync()
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
