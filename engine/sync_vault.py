#!/usr/bin/env python3
"""Sync Obsidian vault markdown files straight into Turso Cloud.

Uses a direct Turso HTTP connection (no embedded replica) so this script
never contends with the server process for the local-replica.db WAL.
A crash here cannot poison the server's replica file.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import yaml
import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

VAULT_ROOT = Path(
    os.getenv("BMFOTE_VAULT_ROOT", str(Path.home() / "dev" / "claude-vault"))
).expanduser()
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown."""
    match = re.match(r'^---\n(.+?)\n---\n?(.*)', text, re.DOTALL)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, match.group(2)


def classify_doc(path: Path, vault_root: Path) -> str:
    """Classify document type based on path and name."""
    rel = path.relative_to(vault_root)
    parts = rel.parts

    if len(parts) >= 2 and parts[-2] == "sessions":
        return "session"
    if len(parts) >= 2 and parts[-2] == "specs":
        return "spec"
    if len(parts) == 2 and path.stem == parts[0]:
        return "hub"
    return "reference"


def get_conn():
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)


def sync():
    if not VAULT_ROOT.exists():
        print(f"Vault root not found: {VAULT_ROOT}")
        print("Set BMFOTE_VAULT_ROOT or ensure ~/dev/claude-vault exists.")
        return

    conn = get_conn()

    synced = 0
    skipped = 0
    existing_paths = set()

    for md_file in VAULT_ROOT.rglob("*.md"):
        if ".obsidian" in str(md_file):
            continue

        rel_path = str(md_file.relative_to(VAULT_ROOT))
        existing_paths.add(rel_path)

        content = md_file.read_text(encoding="utf-8")
        checksum = hashlib.md5(content.encode()).hexdigest()

        # Skip unchanged files
        row = conn.execute(
            "SELECT checksum FROM vault_docs WHERE file_path = ?",
            (rel_path,)
        ).fetchone()
        if row and row[0] == checksum:
            skipped += 1
            continue

        fm, body = parse_frontmatter(content)
        doc_type = classify_doc(md_file, VAULT_ROOT)

        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        conn.execute("""
            INSERT OR REPLACE INTO vault_docs
            (file_path, project, topic, date, outcome, tags,
             doc_type, content, frontmatter_json, last_modified, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rel_path,
            fm.get("project"),
            fm.get("topic"),
            str(fm.get("date", "")),
            fm.get("outcome"),
            json.dumps(tags),
            doc_type,
            body,
            json.dumps(fm, default=str),
            md_file.stat().st_mtime,
            checksum,
        ))
        synced += 1

    # Remove docs that no longer exist in vault
    db_rows = conn.execute("SELECT file_path FROM vault_docs").fetchall()
    db_paths = {row[0] for row in db_rows}
    removed = db_paths - existing_paths
    for path in removed:
        conn.execute("DELETE FROM vault_docs WHERE file_path = ?", (path,))

    conn.commit()

    print(f"Vault sync: {synced} updated, {skipped} unchanged, {len(removed)} removed")


if __name__ == "__main__":
    sync()
