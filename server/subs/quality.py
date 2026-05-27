"""Timing quality check for the reading-rate adapter.

After `adapt_events_for_cps` rewrites translated cues to keep CPS readable,
we want one number per job that says "did the adapt pass succeed?" — so the
user can spot jobs where readability bailed out (500ms floor) or where the
duration redistribution drifted.

Four signals, blended into a 0-100 score:

1. **Readability conformance** — % of adapted events with cps <= max_cps.
   Adapter's whole job. Bailout cases (the 500ms floor) leave the original
   over-CPS event in place; this metric catches them.
2. **Source-span drift** — per-source-cue, sum(adapted children durations)
   vs source duration. Should be ~0 by construction (last chunk pinned).
   Surfaces regression bugs in the split math.
3. **CPS distribution shape** — p50/p95/p99 of adapted CPS. Tells you not
   just pass/fail but how close to the limit the bulk of cues sit.
4. **Boundary drift** — per-source-cue, |adapted first.start - source.start|
   and |adapted last.end - source.end|. Catches off-by-one or rounding bugs.

The adapter is a pure function and never reorders or merges across source
cues, so children of source[i] are an adjacent left-to-right contiguous run
in `adapted`. The mapping algorithm walks `adapted` consuming children whose
`end_ms <= source[i].end_ms`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from server.subs.reading_rate import SubEvent

__all__ = [
    "QualityGateFailed",
    "TimingQuality",
    "bucket_badge",
    "compute_quality",
]


@dataclass(frozen=True)
class TimingQuality:
    """Per-job timing-quality readout. Persisted on Job; surfaced via API + UI.

    `score` is the headline number (0-100, higher = better). Components are
    kept on the dataclass so the UI can render a breakdown panel without a
    second round-trip.
    """

    score: float
    badge: str  # "green" | "yellow" | "red"

    # 1. Readability conformance
    conformance_pct: float          # % of adapted events with cps <= max_cps
    overrun_count: int              # # of adapted events still over max_cps
    max_cps_observed: float         # worst offender's CPS

    # 2. Source-span drift (regression guard — expect ~0)
    span_drift_max_ms: int
    span_drift_mean_ms: float

    # 3. CPS distribution
    cps_p50: float
    cps_p95: float
    cps_p99: float

    # 4. Boundary drift
    boundary_drift_max_ms: int
    boundary_drift_mean_ms: float

    # Sample sizes — useful for the UI breakdown.
    source_event_count: int
    adapted_event_count: int
    max_cps_target: int

    def to_dict(self) -> dict:
        return asdict(self)


class QualityGateFailed(Exception):
    """Raised when a job's timing-quality score is below the configured threshold."""

    def __init__(self, quality: TimingQuality, threshold: float) -> None:
        super().__init__(
            f"timing_quality_gate_failed: score={quality.score:.1f} < threshold={threshold:.1f}"
        )
        self.quality = quality
        self.threshold = threshold


def compute_quality(
    source: list[SubEvent],
    adapted: list[SubEvent],
    max_cps: int,
) -> TimingQuality:
    """Compute TimingQuality comparing pre-adapt source events to post-adapt output.

    `source` is the list passed INTO `adapt_events_for_cps` (i.e. translated
    text on source timings). `adapted` is the function's return value.

    Both lists are assumed sorted by start_ms (pipeline guarantee). The
    adapter never reorders across source cues, so each source cue maps to
    an adjacent contiguous span of adapted cues.
    """
    if not source:
        return _empty_quality(max_cps)

    # === Map source -> contiguous adapted children ===
    groups = _group_adapted_by_source(source, adapted)

    # === 1. Readability conformance ===
    overrun_count = 0
    max_cps_observed = 0.0
    cps_values: list[float] = []
    for ev in adapted:
        cps = ev.cps
        cps_values.append(cps)
        if cps > max_cps:
            overrun_count += 1
        if cps > max_cps_observed:
            max_cps_observed = cps
    conformance_pct = (
        100.0 * (len(adapted) - overrun_count) / len(adapted) if adapted else 100.0
    )

    # === 2. Source-span drift ===
    span_drifts: list[int] = []
    for src, kids in groups:
        if not kids:
            continue
        adapted_span = sum(k.end_ms - k.start_ms for k in kids)
        source_span = src.end_ms - src.start_ms
        span_drifts.append(abs(adapted_span - source_span))
    span_drift_max_ms = max(span_drifts) if span_drifts else 0
    span_drift_mean_ms = (
        sum(span_drifts) / len(span_drifts) if span_drifts else 0.0
    )

    # === 3. CPS distribution ===
    cps_p50 = _percentile(cps_values, 50)
    cps_p95 = _percentile(cps_values, 95)
    cps_p99 = _percentile(cps_values, 99)

    # === 4. Boundary drift ===
    boundary_drifts: list[int] = []
    for src, kids in groups:
        if not kids:
            continue
        start_drift = abs(kids[0].start_ms - src.start_ms)
        end_drift = abs(kids[-1].end_ms - src.end_ms)
        boundary_drifts.extend([start_drift, end_drift])
    boundary_drift_max_ms = max(boundary_drifts) if boundary_drifts else 0
    boundary_drift_mean_ms = (
        sum(boundary_drifts) / len(boundary_drifts) if boundary_drifts else 0.0
    )

    # === Score (0-100, additive penalties from 100) ===
    score = 100.0
    score -= (100.0 - conformance_pct)                              # readability dominates
    score -= max(0, span_drift_max_ms - 50) * 5                     # regression guard tail
    score -= max(0.0, cps_p95 - max_cps) * 2.0                      # distribution tail
    score -= max(0, boundary_drift_max_ms - 100) / 10.0             # boundary drift tail
    score = max(0.0, min(100.0, score))

    return TimingQuality(
        score=round(score, 2),
        badge=bucket_badge(score),
        conformance_pct=round(conformance_pct, 2),
        overrun_count=overrun_count,
        max_cps_observed=round(max_cps_observed, 2),
        span_drift_max_ms=span_drift_max_ms,
        span_drift_mean_ms=round(span_drift_mean_ms, 2),
        cps_p50=round(cps_p50, 2),
        cps_p95=round(cps_p95, 2),
        cps_p99=round(cps_p99, 2),
        boundary_drift_max_ms=boundary_drift_max_ms,
        boundary_drift_mean_ms=round(boundary_drift_mean_ms, 2),
        source_event_count=len(source),
        adapted_event_count=len(adapted),
        max_cps_target=max_cps,
    )


def bucket_badge(
    score: float,
    *,
    green_threshold: float = 95.0,
    yellow_threshold: float = 80.0,
) -> str:
    """Map a 0-100 score to one of three UI badge buckets."""
    if score >= green_threshold:
        return "green"
    if score >= yellow_threshold:
        return "yellow"
    return "red"


# ===== Helpers =============================================================


def _empty_quality(max_cps: int) -> TimingQuality:
    """Sentinel for jobs with no events. Score 100 by convention (nothing failed)."""
    return TimingQuality(
        score=100.0,
        badge="green",
        conformance_pct=100.0,
        overrun_count=0,
        max_cps_observed=0.0,
        span_drift_max_ms=0,
        span_drift_mean_ms=0.0,
        cps_p50=0.0,
        cps_p95=0.0,
        cps_p99=0.0,
        boundary_drift_max_ms=0,
        boundary_drift_mean_ms=0.0,
        source_event_count=0,
        adapted_event_count=0,
        max_cps_target=max_cps,
    )


def _group_adapted_by_source(
    source: list[SubEvent], adapted: list[SubEvent]
) -> list[tuple[SubEvent, list[SubEvent]]]:
    """Walk `adapted` left-to-right and bucket cues into the source cue whose
    span contains them. Children belong to source[i] when their start_ms is
    within [source[i].start_ms, source[i].end_ms].

    Returns a list of (source_event, [adapted children]) tuples, one per
    source event. Some sources may have an empty child list if `adapted`
    is short (e.g. pipeline produced fewer events for some upstream reason).
    """
    groups: list[tuple[SubEvent, list[SubEvent]]] = [(s, []) for s in source]
    j = 0
    for i, src in enumerate(source):
        next_start = source[i + 1].start_ms if i + 1 < len(source) else None
        while j < len(adapted):
            child = adapted[j]
            # Membership rule: child belongs to src when its start_ms is in
            # [src.start_ms, src.end_ms). Adapter never starts a child
            # before its source event begins, so start_ms >= src.start_ms
            # holds when child is a descendant.
            if child.start_ms < src.start_ms:
                # Shouldn't happen with a well-behaved adapter, but guard
                # against it by skipping (orphan child, no source bucket).
                j += 1
                continue
            if next_start is not None and child.start_ms >= next_start:
                break
            groups[i][1].append(child)
            j += 1
    return groups


def _percentile(values: list[float], p: int) -> float:
    """Linear-interpolated percentile. Empty list returns 0.0."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    # Position on 0-indexed sorted array, p in [0, 100].
    rank = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac
