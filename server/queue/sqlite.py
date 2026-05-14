"""SQLite-backed queue driver. v0.1.5."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from server.db import get_conn
from server.queue.base import Job, JobState

log = structlog.get_logger()


def _now() -> int:
    return int(time.time())


def _row_to_job(row: Any) -> Job:
    return Job(
        id=row["id"],
        dedup_key=row["dedup_key"],
        media_path=row["media_path"],
        source_track_index=row["source_track_index"],
        source_lang=row["source_lang"],
        target_lang=row["target_lang"],
        state=JobState(row["state"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        output_path=row["output_path"],
        error=row["error"],
        cost_cents=row["cost_cents"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        checkpoint_line=row["checkpoint_line"],
        force_flag=bool(row["force_flag"]),
        glossary_id=row["glossary_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


class SQLiteQueue:
    """SQLite-backed Queue implementation."""

    def enqueue(self, job: Job) -> Job:
        conn = get_conn()
        now = _now()
        if not job.id:
            job.id = str(uuid.uuid4())
        job.created_at = now
        job.updated_at = now
        if not job.state:
            job.state = JobState.QUEUED
        conn.execute(
            """
            INSERT INTO jobs (
                id, dedup_key, media_path, source_track_index, source_lang,
                target_lang, state, attempts, max_attempts, output_path, error,
                cost_cents, tokens_in, tokens_out, checkpoint_line, force_flag,
                glossary_id, created_at, updated_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job.id, job.dedup_key, job.media_path, job.source_track_index,
                job.source_lang, job.target_lang, job.state.value, job.attempts,
                job.max_attempts, job.output_path, job.error, job.cost_cents,
                job.tokens_in, job.tokens_out, job.checkpoint_line,
                int(job.force_flag), job.glossary_id, job.created_at,
                job.updated_at, job.finished_at,
            ),
        )
        log.info("job_enqueued", job_id=job.id, media=job.media_path)
        return job

    def find_by_dedup(self, dedup_key: str) -> Job | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM jobs WHERE dedup_key = ? ORDER BY created_at DESC LIMIT 1",
            (dedup_key,),
        ).fetchone()
        return _row_to_job(row) if row else None

    def get(self, job_id: str) -> Job | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def claim_next(self) -> Job | None:
        """Atomic claim: oldest QUEUED job → RUNNING, attempts++."""
        conn = get_conn()
        now = _now()
        # Find oldest queued.
        row = conn.execute(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        # Atomic transition.
        cur = conn.execute(
            """
            UPDATE jobs SET state='running', attempts=attempts+1, updated_at=?
            WHERE id=? AND state='queued'
            """,
            (now, row["id"]),
        )
        if cur.rowcount != 1:
            # Lost race; another worker grabbed it.
            return None
        return _row_to_job(conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def update_state(
        self, job_id: str, state: JobState, *, error: str | None = None
    ) -> None:
        conn = get_conn()
        if error is not None:
            conn.execute(
                "UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?",
                (state.value, error, _now(), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET state=?, updated_at=? WHERE id=?",
                (state.value, _now(), job_id),
            )

    def checkpoint(
        self,
        job_id: str,
        *,
        last_completed_line: int,
        cost_cents_delta: int = 0,
        tokens_in_delta: int = 0,
        tokens_out_delta: int = 0,
    ) -> None:
        conn = get_conn()
        conn.execute(
            """
            UPDATE jobs SET
                checkpoint_line=?,
                cost_cents=cost_cents+?,
                tokens_in=tokens_in+?,
                tokens_out=tokens_out+?,
                updated_at=?
            WHERE id=?
            """,
            (
                last_completed_line,
                cost_cents_delta,
                tokens_in_delta,
                tokens_out_delta,
                _now(),
                job_id,
            ),
        )

    def finish(
        self,
        job_id: str,
        *,
        output_path: str,
        cost_cents: int,
        tokens_in: int,
        tokens_out: int,
        output_events: int,
    ) -> None:
        conn = get_conn()
        now = _now()
        conn.execute(
            """
            UPDATE jobs SET
                state='done', output_path=?, cost_cents=?, tokens_in=?,
                tokens_out=?, finished_at=?, updated_at=?
            WHERE id=?
            """,
            (output_path, cost_cents, tokens_in, tokens_out, now, now, job_id),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        conn = get_conn()
        now = _now()
        # Decide state: retry-eligible or terminal.
        row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        if row["attempts"] < row["max_attempts"]:
            conn.execute(
                "UPDATE jobs SET state='retrying', error=?, updated_at=? WHERE id=?",
                (error, now, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs SET state='failed', error=?, finished_at=?, updated_at=?
                WHERE id=?
                """,
                (error, now, now, job_id),
            )

    def reset_orphaned_running_jobs(self) -> int:
        """Server-start recovery: reset stuck RUNNING jobs to QUEUED."""
        conn = get_conn()
        cur = conn.execute(
            "UPDATE jobs SET state='queued', updated_at=? WHERE state='running'",
            (_now(),),
        )
        if cur.rowcount > 0:
            log.info("reset_orphaned_running", count=cur.rowcount)
        return cur.rowcount


_singleton: SQLiteQueue | None = None


def get_queue() -> SQLiteQueue:
    global _singleton
    if _singleton is None:
        _singleton = SQLiteQueue()
        # Recovery sweep on first access.
        _singleton.reset_orphaned_running_jobs()
    return _singleton


def reset_for_tests() -> None:
    """Test-only helper."""
    global _singleton
    _singleton = None
