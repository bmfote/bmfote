-- cctx schema — Turso Cloud (libSQL with standard FTS5)
-- Turso Cloud runs SQLite 3.45.1 with full FTS5 support.
-- Same FTS5 syntax as local SQLite: bm25(), highlight(), MATCH.
-- Only difference from local: no WAL pragma (Turso manages replication).

-- =============================================================
-- Core tables
-- =============================================================

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  session_id TEXT UNIQUE NOT NULL,
  project TEXT,
  first_message_at TIMESTAMP,
  last_message_at TIMESTAMP,
  message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  session_id TEXT NOT NULL,
  parent_uuid TEXT,
  type TEXT NOT NULL,
  role TEXT,
  content TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  timestamp TIMESTAMP,
  workspace_id TEXT NOT NULL DEFAULT 'cctx-default',
  FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);


-- =============================================================
-- Standard indexes
-- =============================================================

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);
CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace_id);

-- =============================================================
-- FTS5 virtual tables (standard SQLite FTS5 — works on Turso Cloud)
-- =============================================================

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id
);

-- =============================================================
-- FTS5 sync triggers (keep FTS in sync with base tables)
-- =============================================================

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
