-- 001_backfill_workspace.sql
-- One-shot migration: promote sessions.project to messages.workspace_id for
-- historical rows tagged 'cctx-default'. Safe to re-run — only touches rows
-- that still carry the default sentinel.
--
-- Usage:
--   POST /api/admin/backfill-workspace  (Authorization: Bearer $API_TOKEN)
-- Or direct on the DB:
--   turso db shell <db> < engine/migrations/001_backfill_workspace.sql

UPDATE messages
   SET workspace_id = COALESCE(
     (SELECT s.project FROM sessions s WHERE s.session_id = messages.session_id),
     'cctx-default'
   )
 WHERE workspace_id = 'cctx-default'
   AND EXISTS (
     SELECT 1 FROM sessions s
      WHERE s.session_id = messages.session_id
        AND s.project IS NOT NULL
        AND s.project != ''
   );
