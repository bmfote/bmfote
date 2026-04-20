# Code audit — validated issues in engine/

14 issues identified by systematic code review, ordered by severity.
The agent should use these as seeds for proposing improvements.

## Critical (2)

1. **N+1 query in error resolution** — `engine/server.py:203-209`
   `query_similar_error()` fetches error matches, then loops issuing one SELECT per match to find solutions. With limit=5, that is 6 queries; with limit=20, 21. Fix with a single LEFT JOIN.

2. **CORS wildcard** — `engine/server.py:81-86`
   `allow_origins=["*"]` exposes all endpoints to cross-origin requests from any domain. Restrict to specific origins or make configurable via env var.

## High (7)

3. **Missing composite indexes** — `engine/schema.sql:40-43`
   Queries filter by `(workspace_id, parent_uuid)`, `(session_id, workspace_id)`, `(workspace_id, type)` but only have single-column indexes. Add multi-column indexes on common filter patterns.

4. **Thread-unsafe global connection singleton** — `engine/db.py:38-71`
   Module-level `_conn` is accessed/modified without locks. FastAPI uses thread pools for sync endpoints — concurrent requests can race on the singleton.

5. **Error message information leakage** — `engine/server.py:300, 314`
   Exception details returned directly to clients: `"error": f"invalid search query: {e}"`. Leaks database errors, query syntax, implementation details.

6. **Full session context fetched and filtered in Python** — `engine/server.py:243-253`
   `query_message()` fetches all messages in a session into Python, then filters by timestamp in a list comprehension. Push filtering to SQL with ORDER BY + LIMIT.

7. **Missing return type hints** — `engine/server.py:159, 183, 221, 256`
   Core query functions lack return type hints. Add `-> list[dict]` or `-> dict | None`.

8. **Duplicate get_conn() in sync_conversations** — `engine/sync_conversations.py:77-78`
   Defines its own `get_conn()` instead of importing from `engine.db`. Two divergent connection strategies.

9. **Inconsistent Optional notation** — `engine/server.py:159, 183, 221, 256`
   Parameters use `type: str = None` instead of `type: str | None = None`. Type checkers reject the former.

## Medium (4)

10. **Non-constant-time token comparison** — `engine/server.py:98`
    `auth[7:] != API_TOKEN` is a direct string comparison. Use `hmac.compare_digest()` for timing-safe comparison.

11. **Bare except clauses** — `engine/mcp_server.py:61,91` and `engine/db.py:55,69`
    `except Exception:` swallows all exceptions. Catch specific exceptions (`sqlite3.OperationalError`, etc.).

12. **Missing input validation** — `engine/server.py:290-296`
    String parameters lack length limits. Add `Query(max_length=500)` or similar bounds.

13. **Silent WAL checkpoint failures** — `engine/db.py:53-58`
    WAL checkpoint failures silently ignored with bare except. At minimum, log the error.

## Low (1)

14. **RAILWAY_ENVIRONMENT backward compat cruft** — `engine/db.py:20-23`
    `os.getenv("RAILWAY_ENVIRONMENT")` fallback is dead code if `CCTX_REMOTE_DB` is set. Remove after confirming all deployments migrated.
