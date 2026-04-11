#!/usr/bin/env python3
"""Sync Obsidian vault markdown files into Turso via embedded replica."""

import json
import hashlib
import os
import re
from pathlib import Path

import yaml
import libsql_experimental as libsql
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

VAULT_ROOT = Path.home() / "dev" / "claude-vault"
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
REPLICA_PATH = Path(__file__).parent / "local-replica.db"


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


def extract_wikilinks(content: str) -> list[tuple[str, str]]:
    """Pull [[target|display]] links from markdown."""
    links = []
    for match in re.finditer(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', content):
        target = match.group(1)
        display = match.group(2) or target
        links.append((target, display))
    return links


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
    conn = libsql.connect(
        database=str(REPLICA_PATH),
        sync_url=TURSO_URL,
        auth_token=TURSO_TOKEN,
    )
    conn.sync()
    return conn


def sync():
    if not VAULT_ROOT.exists():
        print(f"Vault root not found: {VAULT_ROOT}")
        print("Set VAULT_ROOT or ensure ~/dev/claude-vault exists.")
        return

    conn = get_conn()

    synced = 0
    skipped = 0
    links_added = 0
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

        # Update wiki-link edges
        conn.execute("DELETE FROM vault_links WHERE source_path = ?", (rel_path,))
        for target, display in extract_wikilinks(content):
            conn.execute(
                "INSERT INTO vault_links (source_path, target_path, link_text) VALUES (?, ?, ?)",
                (rel_path, target, display)
            )
            links_added += 1

    # Remove docs that no longer exist in vault
    db_rows = conn.execute("SELECT file_path FROM vault_docs").fetchall()
    db_paths = {row[0] for row in db_rows}
    removed = db_paths - existing_paths
    for path in removed:
        conn.execute("DELETE FROM vault_docs WHERE file_path = ?", (path,))
        conn.execute("DELETE FROM vault_links WHERE source_path = ?", (path,))

    conn.commit()
    conn.sync()

    print(f"Vault sync: {synced} updated, {skipped} unchanged, {len(removed)} removed, {links_added} links")


if __name__ == "__main__":
    sync()
