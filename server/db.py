"""SQLite connection + hand-rolled migrations.

v0.1.5 introduces this module. v0.4 will extend MIGRATIONS for glossaries.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger()


def _db_path() -> Path:
    """Resolve DB path. Defaults to <APP_DATA>/translarr.db.

    APP_DATA = $TRANSLARR_DATA_DIR if set, else ./data (CWD-relative for dev).
    """
    import os

    base = os.environ.get("TRANSLARR_DATA_DIR")
    root = Path(base) if base else Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "translarr.db"


_conn: sqlite3.Connection | None = None


def db_path() -> Path:
    """Public accessor for the live DB path. Used by /backup and tests."""
    return _db_path()


def online_backup() -> bytes:
    """Return a consistent point-in-time copy of the database as bytes.

    Uses the SQLite Online Backup API so the snapshot is consistent even
    while the worker is mid-transaction (vs. ``shutil.copyfile`` which
    can produce a corrupted file under WAL mode if a write lands during
    the copy). Returns the .db bytes for the caller to stream as a
    file download.
    """
    src = get_conn()
    import contextlib
    import tempfile

    # SQLite's backup API needs a destination Connection. Use a tempfile
    # destination and slurp the bytes — :memory: connections can't
    # easily serialise.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


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


def _m002_app_settings(conn: sqlite3.Connection) -> None:
    """v0.6.5: app_settings table for runtime-mutable config overrides.

    Each row is a single key whose value overrides the env-baseline in
    server.config.settings. Stored as JSON text so int/bool/string all
    round-trip without per-type columns.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )


def _m003_glossaries(conn: sqlite3.Connection) -> None:
    """Glossary entries for consistent name/term translation.

    Each glossary has an id (scoped to a series or global), and entries
    map a source term to a target-language translation.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS glossaries (
            id TEXT NOT NULL,
            source_term TEXT NOT NULL,
            target_lang TEXT NOT NULL DEFAULT 'en',
            translation TEXT NOT NULL,
            notes TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (id, source_term, target_lang)
        )
    """)


def _m004_series_config(conn: sqlite3.Connection) -> None:
    """Per-series language and translation overrides.

    When a series is looked up by path prefix, its source_lang/target_lang
    overrides the global defaults. The `id` matches glossary IDs so a series
    can have both a glossary and language config under the same name.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_config (
            id TEXT PRIMARY KEY,
            source_lang TEXT,
            target_lang TEXT,
            llm_provider TEXT,
            llm_model TEXT,
            path_prefix TEXT,
            auto_translate INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_series_config_path ON series_config(path_prefix)"
    )


def _m005_timing_quality(conn: sqlite3.Connection) -> None:
    """Per-job timing-quality readout for the reading-rate adapter (TR-wzj).

    Two columns: a headline score for cheap filtering/sorting + the full
    component breakdown as JSON for the UI to render without a second
    round-trip. Both nullable so legacy rows (translated before this
    migration) stay valid.
    """
    conn.execute("ALTER TABLE jobs ADD COLUMN timing_quality_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN timing_quality_json TEXT")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema (jobs + daily_usage)", _m001_initial),
    (2, "app_settings (runtime config overrides)", _m002_app_settings),
    (3, "glossaries table", _m003_glossaries),
    (4, "series_config table (per-series overrides)", _m004_series_config),
    (5, "timing_quality columns on jobs", _m005_timing_quality),
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
