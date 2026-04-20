-- Migration 003: tracked_files table
-- Team-shareable registry of which files are tracked per workspace.

CREATE TABLE IF NOT EXISTS tracked_files (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  tracked_by_session TEXT,
  UNIQUE(workspace_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_tracked_files_ws
  ON tracked_files(workspace_id);
