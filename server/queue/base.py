"""Queue interface + Job model. v0.1.5 ships the SQLite driver.

The interface is designed so a Redis driver (v0.6+ multi-process) is a swap, not a
rewrite. Keep claim/update/checkpoint atomic; everything above the interface is
storage-agnostic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    dedup_key: str
    media_path: str
    target_lang: str
    state: JobState
    source_track_index: int | None = None
    source_lang: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    output_path: str | None = None
    error: str | None = None
    cost_cents: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    checkpoint_line: int = 0
    force_flag: bool = False
    glossary_id: str | None = None
    created_at: int = 0
    updated_at: int = 0
    finished_at: int | None = None


def compute_dedup_key(media_path: str, source_track_index: int | None, target_lang: str) -> str:
    """Stable hash of (media_path, source_track_index, target_lang). v0.1.5+."""
    norm_path = str(Path(media_path).resolve()) if Path(media_path).is_absolute() else media_path
    payload = f"{norm_path}|{source_track_index or -1}|{target_lang}"
    return hashlib.sha256(payload.encode()).hexdigest()


class Queue(Protocol):
    """Storage-agnostic job queue interface."""

    def enqueue(self, job: Job) -> Job:
        """Insert job. Returns the persisted job (with timestamps populated)."""
        ...

    def find_by_dedup(self, dedup_key: str) -> Job | None:
        """Return existing job (any state) matching dedup_key, or None."""
        ...

    def get(self, job_id: str) -> Job | None: ...

    def claim_next(self) -> Job | None:
        """Atomically transition one QUEUED job to RUNNING and return it.

        Returns None if no job is queued.
        """
        ...

    def update_state(
        self, job_id: str, state: JobState, *, error: str | None = None
    ) -> None: ...

    def checkpoint(
        self,
        job_id: str,
        *,
        last_completed_line: int,
        cost_cents_delta: int = 0,
        tokens_in_delta: int = 0,
        tokens_out_delta: int = 0,
    ) -> None: ...

    def finish(
        self,
        job_id: str,
        *,
        output_path: str,
        cost_cents: int,
        tokens_in: int,
        tokens_out: int,
        output_events: int,
    ) -> None: ...

    def mark_failed(self, job_id: str, error: str) -> None: ...

    def reset_orphaned_running_jobs(self) -> int:
        """On server start, transition any RUNNING jobs back to QUEUED for re-claim.

        Returns count reset.
        """
        ...
