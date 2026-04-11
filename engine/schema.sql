-- bmfote schema — Turso Cloud (libSQL with standard FTS5)
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
  FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS tool_uses (
  id INTEGER PRIMARY KEY,
  message_uuid TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tool_name TEXT,
  tool_input_summary TEXT,
  timestamp TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vault_docs (
  id INTEGER PRIMARY KEY,
  file_path TEXT UNIQUE NOT NULL,
  project TEXT,
  topic TEXT,
  date TEXT,
  outcome TEXT,
  tags TEXT,
  doc_type TEXT,
  content TEXT,
  frontmatter_json TEXT,
  last_modified REAL,
  checksum TEXT
);

CREATE TABLE IF NOT EXISTS vault_links (
  id INTEGER PRIMARY KEY,
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  link_text TEXT
);

-- =============================================================
-- Standard indexes
-- =============================================================

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);
CREATE INDEX IF NOT EXISTS idx_tool_uses_session ON tool_uses(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_tool ON tool_uses(tool_name);
CREATE INDEX IF NOT EXISTS idx_vault_docs_project ON vault_docs(project);
CREATE INDEX IF NOT EXISTS idx_vault_docs_type ON vault_docs(doc_type);
CREATE INDEX IF NOT EXISTS idx_vault_links_source ON vault_links(source_path);
CREATE INDEX IF NOT EXISTS idx_vault_links_target ON vault_links(target_path);

-- =============================================================
-- FTS5 virtual tables (standard SQLite FTS5 — works on Turso Cloud)
-- =============================================================

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id
);

CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(
  topic,
  tags,
  content,
  project,
  content=vault_docs,
  content_rowid=id
);

-- =============================================================
-- FTS5 sync triggers (keep FTS in sync with base tables)
-- =============================================================

-- Messages FTS triggers
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

-- Vault FTS triggers
CREATE TRIGGER IF NOT EXISTS vault_ai AFTER INSERT ON vault_docs BEGIN
  INSERT INTO vault_fts(rowid, topic, tags, content, project)
    VALUES (new.id, new.topic, new.tags, new.content, new.project);
END;

CREATE TRIGGER IF NOT EXISTS vault_ad AFTER DELETE ON vault_docs BEGIN
  INSERT INTO vault_fts(vault_fts, rowid, topic, tags, content, project)
    VALUES('delete', old.id, old.topic, old.tags, old.content, old.project);
END;

CREATE TRIGGER IF NOT EXISTS vault_au AFTER UPDATE ON vault_docs BEGIN
  INSERT INTO vault_fts(vault_fts, rowid, topic, tags, content, project)
    VALUES('delete', old.id, old.topic, old.tags, old.content, old.project);
  INSERT INTO vault_fts(rowid, topic, tags, content, project)
    VALUES (new.id, new.topic, new.tags, new.content, new.project);
END;
