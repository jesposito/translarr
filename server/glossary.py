"""Glossary persistence — consistent name/term translation.

Glossaries map source-language terms to target-language translations so
the LLM keeps character names, attack names, and world-specific terms
consistent across episodes and batches.

Each glossary has an ``id`` that scopes it (e.g., a series name or
a global namespace). Entries are keyed by ``(glossary_id, source_term,
target_lang)`` so the same term can have different translations per
target language.

The pipeline reads from here when constructing the LLM prompt's
glossary section.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from server.db import get_conn

log = structlog.get_logger()


def list_glossaries() -> list[dict[str, Any]]:
    """Return all distinct glossary IDs with entry counts."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, COUNT(*) AS entry_count, MAX(updated_at) AS last_updated
        FROM glossaries
        GROUP BY id
        ORDER BY id
        """
    ).fetchall()
    return [{"id": r["id"], "entry_count": r["entry_count"], "last_updated": r["last_updated"]} for r in rows]


def get_glossary(glossary_id: str) -> list[dict[str, Any]]:
    """Return all entries for a glossary."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT source_term, target_lang, translation, notes, created_at, updated_at FROM glossaries WHERE id = ? ORDER BY source_term",
        (glossary_id,),
    ).fetchall()
    return [
        {
            "source_term": r["source_term"],
            "target_lang": r["target_lang"],
            "translation": r["translation"],
            "notes": r["notes"],
        }
        for r in rows
    ]


def get_glossary_map(glossary_id: str, target_lang: str = "en") -> dict[str, str]:
    """Return a flat {source_term: translation} dict for the pipeline.

    This is what the LLM router receives as the ``glossary`` parameter.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT source_term, translation FROM glossaries WHERE id = ? AND target_lang = ?",
        (glossary_id, target_lang),
    ).fetchall()
    return {r["source_term"]: r["translation"] for r in rows}


def upsert_entry(
    glossary_id: str,
    source_term: str,
    translation: str,
    target_lang: str = "en",
    notes: str | None = None,
) -> None:
    """Insert or update a single glossary entry."""
    conn = get_conn()
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO glossaries (id, source_term, target_lang, translation, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id, source_term, target_lang) DO UPDATE SET
            translation = excluded.translation,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (glossary_id, source_term, target_lang, translation, notes, now, now),
    )
    log.info("glossary_upsert", glossary_id=glossary_id, source_term=source_term, target_lang=target_lang)


def delete_entry(glossary_id: str, source_term: str, target_lang: str = "en") -> bool:
    """Delete a single glossary entry. Returns True if a row was deleted."""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM glossaries WHERE id = ? AND source_term = ? AND target_lang = ?",
        (glossary_id, source_term, target_lang),
    )
    return cur.rowcount > 0


def delete_glossary(glossary_id: str) -> int:
    """Delete an entire glossary. Returns the number of rows deleted."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM glossaries WHERE id = ?", (glossary_id,))
    return cur.rowcount


def import_entries(glossary_id: str, entries: list[dict[str, str]], target_lang: str = "en") -> int:
    """Bulk import entries. Each entry: {source_term, translation, notes?}.

    Returns the number of entries imported.
    """
    count = 0
    for entry in entries:
        source = entry.get("source_term", "").strip()
        trans = entry.get("translation", "").strip()
        if not source or not trans:
            continue
        notes = entry.get("notes", "").strip() or None
        upsert_entry(glossary_id, source, trans, target_lang, notes)
        count += 1
    log.info("glossary_import", glossary_id=glossary_id, count=count)
    return count
