-- 002_definition_edits.sql
-- Phase 1 killer-app layer: AI-proposed edits to canonical markdown files
-- (icp.md, playbook.md, etc.) queued for human review with session provenance.
-- Local files remain source-of-truth; this table stores AI-proposed edits only.
--
-- Usage:
--   turso db shell <db> < engine/migrations/002_definition_edits.sql
-- Local dev:
--   sqlite3 engine/local-replica.db < engine/migrations/002_definition_edits.sql

CREATE TABLE IF NOT EXISTS definition_edits (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  workspace_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  old_content TEXT,
  new_content TEXT NOT NULL,
  reason TEXT,
  confidence REAL,
  source_session_id TEXT NOT NULL,
  source_message_uuid TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TIMESTAMP,
  FOREIGN KEY (source_session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_def_edits_ws_status
  ON definition_edits(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_def_edits_ws_file_status
  ON definition_edits(workspace_id, file_path, status);
CREATE INDEX IF NOT EXISTS idx_def_edits_session
  ON definition_edits(source_session_id);
