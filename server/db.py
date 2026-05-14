"""SQLite connection + hand-rolled migrations.

v0.1.5 introduces this module. v0.4 will extend MIGRATIONS for glossaries.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog

from server.config import settings

log = structlog.get_logger()


def _db_path() -> Path:
    """Resolve DB path. Defaults to <APP_DATA>/translarr.db.

    APP_DATA = $TRANSLARR_DATA_DIR if set, else ./data (CWD-relative for dev).
    """
    import os

    base = os.environ.get("TRANSLARR_DATA_DIR")
    if base:
        root = Path(base)
    else:
        root = Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "translarr.db"


_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """Return process-wide singleton SQLite connection. Initializes on first call."""
    global _conn
    if _conn is None:
        path = _db_path()
        log.info("db_open", path=str(path))
        _conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        apply_pending_migrations(_conn)
    return _conn


def close_for_tests() -> None:
    """Test-only: close + clear the singleton so a fresh tmpdir DB can be opened."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# === Migrations ===========================================================

def _m001_initial(conn: sqlite3.Connection) -> None:
    """v0.1.5: jobs + daily_usage tables. Statements run one-by-one for autocommit compat."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            dedup_key TEXT NOT NULL,
            media_path TEXT NOT NULL,
            source_track_index INTEGER,
            source_lang TEXT,
            target_lang TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            output_path TEXT,
            error TEXT,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            checkpoint_line INTEGER NOT NULL DEFAULT 0,
            force_flag INTEGER NOT NULL DEFAULT 0,
            glossary_id TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            finished_at INTEGER
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedup_key)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)",
        """
        CREATE TABLE IF NOT EXISTS daily_usage (
            day TEXT PRIMARY KEY,
            spent_cents INTEGER NOT NULL DEFAULT 0
        )
        """,
    ]
    for stmt in statements:
        conn.execute(stmt)


MIGRATIONS: list[tuple[int, str, callable]] = [
    (1, "initial schema (jobs + daily_usage)", _m001_initial),
]


def apply_pending_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )
    """)
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version")}
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        log.info("migration_apply", version=version, name=name)
        try:
            conn.execute("BEGIN")
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, int(time.time())),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
