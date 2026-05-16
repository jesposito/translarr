"""Per-series configuration overrides.

When a series or movie directory has specific language needs (e.g., a Korean
drama that should always translate from Korean to English, while the global
default targets German), series_config stores per-path overrides.

The ``id`` field matches glossary IDs so a series can have both a term
dictionary and language preferences under the same name.

Lookup strategy: given a media_path, find the series_config whose
``path_prefix`` is the longest prefix match.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from server.db import get_conn

log = structlog.get_logger()


def list_series() -> list[dict[str, Any]]:
    """Return all series configs."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, source_lang, target_lang, llm_provider, llm_model, "
        "path_prefix, auto_translate, created_at, updated_at FROM series_config "
        "ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_series(series_id: str) -> dict[str, Any] | None:
    """Return a single series config by ID."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, source_lang, target_lang, llm_provider, llm_model, "
        "path_prefix, auto_translate, created_at, updated_at FROM series_config "
        "WHERE id = ?",
        (series_id,),
    ).fetchone()
    return dict(row) if row else None


def lookup_by_path(media_path: str) -> dict[str, Any] | None:
    """Find the best-matching series config for a media path.

    Returns the series whose path_prefix is the longest prefix of media_path.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, source_lang, target_lang, llm_provider, llm_model, "
        "path_prefix, auto_translate, created_at, updated_at FROM series_config "
        "WHERE path_prefix IS NOT NULL"
    ).fetchall()

    best: dict[str, Any] | None = None
    best_len = 0
    for row in rows:
        prefix = row["path_prefix"] or ""
        if media_path.startswith(prefix) and len(prefix) > best_len:
            best = dict(row)
            best_len = len(prefix)
    return best


def upsert_series(
    series_id: str,
    source_lang: str | None = None,
    target_lang: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    path_prefix: str | None = None,
    auto_translate: bool = False,
) -> None:
    """Insert or update a series config."""
    conn = get_conn()
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO series_config (id, source_lang, target_lang, llm_provider, llm_model,
                                   path_prefix, auto_translate, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_lang = excluded.source_lang,
            target_lang = excluded.target_lang,
            llm_provider = excluded.llm_provider,
            llm_model = excluded.llm_model,
            path_prefix = excluded.path_prefix,
            auto_translate = excluded.auto_translate,
            updated_at = excluded.updated_at
        """,
        (series_id, source_lang, target_lang, llm_provider, llm_model,
         path_prefix, 1 if auto_translate else 0, now, now),
    )
    log.info("series_config_upsert", series_id=series_id)


def delete_series(series_id: str) -> bool:
    """Delete a series config. Returns True if a row was deleted."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM series_config WHERE id = ?", (series_id,))
    return cur.rowcount > 0
