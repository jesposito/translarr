"""Tests for the timing-quality readout (TR-wzj).

Covers the four metric signals (conformance, span drift, CPS distribution,
boundary drift), the score formula, the badge bucketing, and the
QualityGateFailed gate.
"""

from server.subs.quality import (
    QualityGateFailed,
    TimingQuality,
    bucket_badge,
    compute_quality,
)
from server.subs.reading_rate import SubEvent, adapt_events_for_cps

# === Bang-on cases ========================================================


def test_empty_input_returns_perfect_score():
    q = compute_quality([], [], max_cps=17)
    assert q.score == 100.0
    assert q.badge == "green"
    assert q.source_event_count == 0
    assert q.adapted_event_count == 0


def test_short_readable_lines_score_100():
    """A subtitle file with no need for adaptation should score a perfect 100."""
    events = [
        SubEvent(start_ms=0, end_ms=3000, text="Short line."),
        SubEvent(start_ms=3500, end_ms=6000, text="Also short."),
        SubEvent(start_ms=7000, end_ms=10000, text="Easy reading."),
    ]
    adapted = adapt_events_for_cps(events, max_cps=17)
    q = compute_quality(events, adapted, max_cps=17)
    assert q.score == 100.0
    assert q.badge == "green"
    assert q.conformance_pct == 100.0
    assert q.overrun_count == 0
    assert q.span_drift_max_ms == 0
    assert q.boundary_drift_max_ms == 0


def test_long_line_split_cleanly_scores_100():
    """A line that the adapter splits successfully into readable chunks
    should ALSO score 100 — the split solved the problem."""
    # 4-second cue, ~60 chars = 15 cps base, but max=10 forces split.
    # After split into ~4 chunks of ~15 chars each over 1s, each chunk
    # is ~15 cps... still over 10. Use a longer duration so chunks land
    # under 10 cps.
    ev = SubEvent(
        start_ms=0,
        end_ms=10_000,
        text="Long line one. Long line two. Long line three. Long line four.",
    )
    adapted = adapt_events_for_cps([ev], max_cps=10)
    q = compute_quality([ev], adapted, max_cps=10)
    # Must score green — all children should be readable.
    assert q.badge == "green", f"expected green, got {q.badge} (score={q.score})"
    assert q.conformance_pct == 100.0
    assert q.span_drift_max_ms == 0  # last child pinned to end_ms by construction
    assert q.boundary_drift_max_ms == 0  # first child starts at source start


# === Failure cases =========================================================


def test_500ms_floor_bailout_drops_score():
    """When the adapter can't split (chunks would be < 500ms) it keeps the
    original over-CPS event. That should surface as overrun_count > 0 and
    a non-green score."""
    # 800ms duration, 30 chars = 37.5 cps. Can't split into 2 readable
    # chunks because each chunk would need to be 400ms (below floor).
    ev = SubEvent(
        start_ms=0,
        end_ms=800,
        text="thirty characters of unreadable",  # 31 chars
    )
    adapted = adapt_events_for_cps([ev], max_cps=15)
    q = compute_quality([ev], adapted, max_cps=15)
    # Adapter bailed → kept the original → overrun.
    assert q.overrun_count >= 1
    assert q.conformance_pct < 100.0
    assert q.max_cps_observed > 15
    assert q.score < 95  # not green
    assert q.badge in {"yellow", "red"}


def test_cps_p95_above_target_penalizes_score():
    """A bunch of just-over-limit events should tank the distribution tail."""
    # Five events each at ~20 cps with 600ms duration (too short to split).
    events = [
        SubEvent(start_ms=i * 1000, end_ms=i * 1000 + 600, text="twelve chars")
        for i in range(5)
    ]
    adapted = adapt_events_for_cps(events, max_cps=15)
    q = compute_quality(events, adapted, max_cps=15)
    assert q.cps_p95 > 15
    assert q.score < 100


# === Span drift (regression guard) =========================================


def test_synthetic_span_drift_surfaces():
    """If we hand-construct an `adapted` list whose children don't sum to
    the source span, span_drift_max_ms should catch it."""
    src = SubEvent(start_ms=0, end_ms=5000, text="Source line text here.")
    # Adapted children sum to 4000ms — 1000ms short of source's 5000ms span.
    adapted = [
        SubEvent(start_ms=0, end_ms=2000, text="Source line"),
        SubEvent(start_ms=2000, end_ms=4000, text="text here."),
    ]
    q = compute_quality([src], adapted, max_cps=17)
    assert q.span_drift_max_ms == 1000
    # 1000ms drift over 50ms threshold = (1000-50)*5 = 4750 penalty → 0 score.
    assert q.score == 0.0
    assert q.badge == "red"


def test_real_adapter_split_has_zero_span_drift():
    """Sanity: the actual adapter (not synthetic) should always produce
    zero span drift because last_chunk.end_ms is pinned to source end_ms."""
    events = [
        SubEvent(start_ms=0, end_ms=4000, text="A. B. C. D. E. F. G. H."),
        SubEvent(start_ms=5000, end_ms=9000, text="One two three four five six seven."),
    ]
    adapted = adapt_events_for_cps(events, max_cps=10)
    q = compute_quality(events, adapted, max_cps=10)
    assert q.span_drift_max_ms == 0
    assert q.span_drift_mean_ms == 0.0


# === Boundary drift =======================================================


def test_synthetic_boundary_drift_surfaces():
    """Boundary drift is detectable independent of span drift.

    Construct two adapted children whose total span matches source but whose
    first child starts 200ms after the source start (i.e. a real boundary
    shift not caused by a span gap)."""
    src = SubEvent(start_ms=1000, end_ms=4000, text="Source line.")
    # Two children: total span = 200 + 2600 = 2800ms but begins 200ms late.
    # To isolate boundary drift, we instead use ONE child shifted later that
    # preserves the source span by extending its end too.
    adapted = [SubEvent(start_ms=1200, end_ms=4200, text="Source line.")]
    q = compute_quality([src], adapted, max_cps=17)
    # Start drift = 200ms, end drift = 200ms → max = 200ms.
    assert q.boundary_drift_max_ms == 200
    # Span preserved → no span penalty.
    assert q.span_drift_max_ms == 0
    # Boundary penalty kicks in only above 100ms: (200-100)/10 = 10 points.
    # Score: 100 - 10 = 90.
    assert q.score < 100.0
    assert q.score >= 89.0


# === Score formula + badges ===============================================


def test_bucket_badge_thresholds():
    assert bucket_badge(100.0) == "green"
    assert bucket_badge(95.0) == "green"
    assert bucket_badge(94.99) == "yellow"
    assert bucket_badge(80.0) == "yellow"
    assert bucket_badge(79.99) == "red"
    assert bucket_badge(0.0) == "red"


def test_bucket_badge_custom_thresholds():
    assert bucket_badge(75.0, green_threshold=70.0, yellow_threshold=50.0) == "green"
    assert bucket_badge(60.0, green_threshold=70.0, yellow_threshold=50.0) == "yellow"
    assert bucket_badge(40.0, green_threshold=70.0, yellow_threshold=50.0) == "red"


def test_conformance_drops_score_proportionally():
    """50% conformance should subtract 50 from the base score."""
    # Mix half-readable and half-overrun. Each overrun event uses a tiny
    # duration so the adapter can't split it.
    events = [
        SubEvent(start_ms=0, end_ms=3000, text="Readable line."),
        SubEvent(start_ms=4000, end_ms=4600, text="twenty long chars yo"),
        SubEvent(start_ms=5000, end_ms=8000, text="Another readable."),
        SubEvent(start_ms=9000, end_ms=9600, text="twenty long chars yo"),
    ]
    adapted = adapt_events_for_cps(events, max_cps=15)
    q = compute_quality(events, adapted, max_cps=15)
    # 2 of 4 over CPS → 50% conformance → score deducts 50 for conformance
    # plus an extra distribution-tail penalty for cps_p95 >> max_cps.
    # The blended score lands somewhere in the red bucket; exact value
    # depends on how far past the limit the overrun lines are.
    assert q.conformance_pct == 50.0
    assert q.score < 60.0
    assert q.badge == "red"


# === Gate exception =======================================================


def test_quality_gate_failed_carries_quality():
    q = TimingQuality(
        score=42.0, badge="red", conformance_pct=50.0, overrun_count=10,
        max_cps_observed=30.0, span_drift_max_ms=0, span_drift_mean_ms=0.0,
        cps_p50=12.0, cps_p95=25.0, cps_p99=30.0,
        boundary_drift_max_ms=0, boundary_drift_mean_ms=0.0,
        source_event_count=20, adapted_event_count=20, max_cps_target=17,
    )
    exc = QualityGateFailed(q, 70.0)
    assert exc.quality is q
    assert exc.threshold == 70.0
    assert "42.0" in str(exc)
    assert "70.0" in str(exc)


# === Group adapted by source ==============================================


def test_group_handles_multi_child_source():
    """A single source cue with many adapted children should still map
    correctly so span_drift is computed against the right group."""
    # 3-second cue with ~90 chars = 30cps, well over max=8. Adapter must split.
    src = SubEvent(
        start_ms=0,
        end_ms=3000,
        text="One sentence here. Two sentences now. Three and four. Five and six maybe.",
    )
    adapted = adapt_events_for_cps([src], max_cps=8)
    # Must have split into multiple children for this test to mean anything.
    assert len(adapted) >= 2, f"adapter did not split: {adapted}"
    q = compute_quality([src], adapted, max_cps=8)
    # All children belong to the one source cue → span drift = 0 by pin.
    assert q.span_drift_max_ms == 0


def test_to_dict_roundtrip():
    """to_dict should produce a JSON-serializable payload."""
    import json
    ev = SubEvent(start_ms=0, end_ms=3000, text="Short.")
    q = compute_quality([ev], [ev], max_cps=17)
    d = q.to_dict()
    s = json.dumps(d)
    parsed = json.loads(s)
    assert parsed["score"] == q.score
    assert parsed["badge"] == q.badge
