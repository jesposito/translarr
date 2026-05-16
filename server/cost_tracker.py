"""In-memory cost tracker for v0.1 kill-switches.

v0.1.5 will replace this with a SQLite `daily_usage` table so counters survive restart.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

log = structlog.get_logger()


class CostCapExceeded(RuntimeError):
    """Raised when daily or per-job cost cap would be exceeded."""


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _try_db_conn():
    """Best-effort: return the SQLite conn if the DB module is initialized OK.

    Returns None and logs loudly when initialisation fails. Cost tracking
    is a safety mechanism (kill-switches enforce daily/per-job caps), so a
    silent failure here is a serious incident — operators need to know
    they're running uncapped. Imports are local because cost_tracker is
    imported by pipeline at module load and we want to avoid pulling in
    server.db until first use.
    """
    try:
        from server.db import get_conn

        return get_conn()
    except Exception:
        log.exception("cost_tracker_db_unavailable")
        return None


def daily_total_cents() -> int:
    conn = _try_db_conn()
    if conn is None:
        return 0
    row = conn.execute(
        "SELECT spent_cents FROM daily_usage WHERE day = ?", (_today(),)
    ).fetchone()
    return row["spent_cents"] if row else 0


def record(cost_cents: int) -> None:
    conn = _try_db_conn()
    if conn is None:
        log.warning("cost_record_no_db", added_cents=cost_cents)
        return
    day = _today()
    conn.execute(
        """
        INSERT INTO daily_usage (day, spent_cents) VALUES (?, ?)
        ON CONFLICT(day) DO UPDATE SET spent_cents = spent_cents + excluded.spent_cents
        """,
        (day, cost_cents),
    )
    log.info("cost_recorded", day=day, added_cents=cost_cents, daily_total=daily_total_cents())


def check_daily_cap(max_daily_cents: int) -> None:
    """Raises if today's spend has already reached the cap. Call BEFORE starting a job."""
    spent = daily_total_cents()
    if spent >= max_daily_cents:
        raise CostCapExceeded(
            f"daily_cost_cap_reached: spent={spent}c >= cap={max_daily_cents}c (today={_today()})"
        )


def check_job_cap(estimated_cents: int, max_job_cents: int) -> None:
    """Raises if a single job's estimated cost exceeds the per-job cap.

    Called mid-job by the pipeline at batch boundaries with running estimate.
    """
    if estimated_cents > max_job_cents:
        raise CostCapExceeded(
            f"per_job_cost_cap_reached: estimated={estimated_cents}c > cap={max_job_cents}c"
        )


# Per-token cost table (cents per million tokens). Approximate; refined when provider returns usage.
# Sourced from public pricing as of 2026-05-14.
COST_TABLE_CENTS_PER_MTOK: dict[str, tuple[int, int]] = {
    # (input_cents_per_million, output_cents_per_million)
    "claude-opus-4-7": (1500, 7500),
    "claude-sonnet-4-6": (300, 1500),
    "claude-haiku-4-5-20251001": (80, 400),
    "claude-haiku-4-5": (80, 400),
    "gpt-5.5": (100, 800),
    "gpt-4o-mini": (15, 60),
    # DeepSeek (2026-05 pricing)
    "deepseek-chat": (27, 110),
    "deepseek-reasoner": (55, 219),
    # Google Gemini (2026-05 pricing)
    "gemini-2.5-flash": (15, 60),
    "gemini-2.5-pro": (125, 1000),
    "gemini-2.0-flash": (10, 40),
}


def is_known_model(model: str) -> bool:
    """True iff the cost tracker has explicit pricing for this model."""
    return model in COST_TABLE_CENTS_PER_MTOK


# One-shot warning so a misconfigured model doesn't fill the log with one
# line per batch. Reset when the model name changes.
_warned_unknown: set[str] = set()


def estimate_cents(model: str, tokens_in: int, tokens_out: int) -> int:
    """Best-effort cost estimate.

    Unknown models fall back to Sonnet pricing — but this is dangerous
    when the actual provider charges Opus rates (e.g. user typed
    'claude-opus-4-5' which Anthropic silently aliases to Opus). We
    warn loudly on first encounter so operators notice the gap. Real
    safety is the per-job + per-day caps, which are enforced on the
    (under-)estimate, so a runaway is still bounded by your $-per-day
    setting, just bounded with the wrong arithmetic.
    """
    rates = COST_TABLE_CENTS_PER_MTOK.get(model)
    if rates is None:
        if model not in _warned_unknown:
            log.warning(
                "cost_estimate_unknown_model",
                model=model,
                fallback="claude-sonnet-4-6 pricing (300/1500 per Mtok)",
                hint="Add the model to COST_TABLE_CENTS_PER_MTOK or set LLM_MODEL to a known one. Daily cap will be enforced on the under-estimate.",
            )
            _warned_unknown.add(model)
        rates = (300, 1500)
    in_rate, out_rate = rates
    return (tokens_in * in_rate + tokens_out * out_rate) // 1_000_000


def reset_for_tests() -> None:
    """Test-only helper. Truncates daily_usage if DB exists."""
    conn = _try_db_conn()
    if conn is not None:
        conn.execute("DELETE FROM daily_usage")
