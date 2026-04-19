-- Migration 004: definition_files table
-- Stores .def file content in the database for team sync.
-- Each row is the latest version of a .def file for a workspace+file_path.

CREATE TABLE IF NOT EXISTS definition_files (
  id INTEGER PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  content TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_by_session TEXT,
  UNIQUE(workspace_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_def_files_ws
  ON definition_files(workspace_id);
