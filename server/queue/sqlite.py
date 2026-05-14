"""SQLite-backed queue driver. v0.1.5."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from server.config import settings
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
        output_path: str | None,
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

    def list_jobs(
        self, state: JobState | None, limit: int, offset: int
    ) -> tuple[int, list[Job]]:
        """Returns (total_matching_count, list_of_jobs_for_page).

        Ordered by created_at DESC. Uses parameterized SQL only.
        `total` is a separate COUNT(*) so paginated clients can report totals.
        """
        conn = get_conn()
        if state is not None:
            total_row = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE state = ?", (state.value,)
            ).fetchone()
            rows = conn.execute(
                """
                SELECT * FROM jobs WHERE state = ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (state.value, limit, offset),
            ).fetchall()
        else:
            total_row = conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
            rows = conn.execute(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        total = int(total_row["c"]) if total_row else 0
        return total, [_row_to_job(r) for r in rows]

    def aggregate_stats(self) -> dict:
        """Returns the {today, all_time, queue} dict for /stats endpoint.

        All single-query reads. Uses parameterized SQL.
        """
        conn = get_conn()
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")

        # Today's cost from daily_usage.
        daily_row = conn.execute(
            "SELECT spent_cents FROM daily_usage WHERE day = ?", (today_str,)
        ).fetchone()
        today_cost = int(daily_row["spent_cents"]) if daily_row else 0

        # Today's job counts: aggregate by state in one query.
        # Compute UTC start-of-day epoch as the cutoff.
        start_of_day_epoch = int(
            datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        )
        today_agg = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done,
              SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN state IN ('queued','running','retrying') THEN 1 ELSE 0 END)
                AS in_flight
            FROM jobs
            WHERE created_at >= ?
            """,
            (start_of_day_epoch,),
        ).fetchone()

        # All-time aggregate.
        all_time_row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(cost_cents), 0) AS cost FROM jobs"
        ).fetchone()

        # Queue depth by state in one query.
        queue_rows = conn.execute(
            """
            SELECT state, COUNT(*) AS c FROM jobs
            WHERE state IN ('queued','running','retrying')
            GROUP BY state
            """
        ).fetchall()
        queue_counts = {"queued": 0, "running": 0, "retrying": 0}
        for r in queue_rows:
            queue_counts[r["state"]] = int(r["c"])

        # Headroom: % of daily cap spent. Lets the Dashboard surface a
        # "78% of today's cap used" hint before the worker starts 429-ing
        # mid-job. Computed server-side so the UI doesn't need to know
        # the cap config.
        cap = settings.max_cost_cents_per_day
        budget_pct_used = (today_cost / cap * 100.0) if cap > 0 else 0.0

        return {
            "today": {
                "date": today_str,
                "cost_cents": today_cost,
                "jobs_count": int(today_agg["total"] or 0),
                "jobs_done": int(today_agg["done"] or 0),
                "jobs_failed": int(today_agg["failed"] or 0),
                "jobs_in_flight": int(today_agg["in_flight"] or 0),
            },
            "all_time": {
                "cost_cents": int(all_time_row["cost"] or 0),
                "jobs_count": int(all_time_row["total"] or 0),
            },
            "queue": queue_counts,
            "budget": {
                "daily_cap_cents": cap,
                "spent_cents": today_cost,
                "remaining_cents": max(0, cap - today_cost),
                "pct_used": round(budget_pct_used, 1),
            },
        }

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
