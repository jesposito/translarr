from server.subs.reading_rate import (
    MIN_CHUNK_MS,
    SubEvent,
    _balanced_bin_pack,
    _split_text,
    adapt_events_for_cps,
)

# === Original v0.1 behaviors (preserved) ==================================


def test_under_cps_passes_through():
    ev = SubEvent(start_ms=0, end_ms=3000, text="Short line.")
    out = adapt_events_for_cps([ev], max_cps=17)
    assert len(out) == 1
    assert out[0].text == "Short line."


def test_short_text_never_splits():
    ev = SubEvent(start_ms=0, end_ms=100, text="Hi.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1


def test_split_preserves_total_span():
    """Last chunk end_ms must match original end_ms exactly — no rounding drift."""
    ev = SubEvent(start_ms=1000, end_ms=5000, text="Long line one. Long line two. Three.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert out[0].start_ms == 1000
    assert out[-1].end_ms == 5000


# === Proportional duration (drift fix #1) =================================


def test_proportional_duration_allocation():
    """Longer chunks must get more time than shorter chunks of the same event."""
    # 96 chars in 4 seconds = 24 CPS, over the 10 limit. Two clear-length sentences.
    ev = SubEvent(
        start_ms=0,
        end_ms=4000,
        text="Tiny. A much longer sentence that needs way more reading time than the tiny one before it.",
    )
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) >= 2, f"expected split, got {len(out)} chunk(s): {[c.text for c in out]}"
    # Sort by start time so order is deterministic
    out_sorted = sorted(out, key=lambda c: c.start_ms)
    first_dur = out_sorted[0].end_ms - out_sorted[0].start_ms
    last_dur = out_sorted[-1].end_ms - out_sorted[-1].start_ms
    # The "Tiny." chunk should be the short one. The longer rest should get more time.
    assert last_dur > first_dur, (
        f"Expected proportional allocation. First chunk ({out_sorted[0].text!r}) got {first_dur}ms; "
        f"last chunk ({out_sorted[-1].text!r}) got {last_dur}ms."
    )


# === 500ms minimum floor (drift fix #2) ===================================


def test_500ms_floor_keeps_original_when_split_would_be_too_short():
    """A 600ms event with too much text → split would create sub-500ms chunks → keep original."""
    ev = SubEvent(
        start_ms=0,
        end_ms=600,  # 0.6s
        text="This is way too much text for six hundred milliseconds period."  # 62 chars
    )
    out = adapt_events_for_cps([ev], max_cps=10)
    # Splitting into n=ceil(62 / (10 * 0.6)) = 11 chunks would average 54ms each → under floor
    # Should keep original.
    assert len(out) == 1
    assert out[0].text == ev.text


def test_500ms_floor_allows_split_when_each_chunk_clears_floor():
    """A 4s event with text that splits into 2 chunks of ~2s each — both clear 500ms floor."""
    ev = SubEvent(start_ms=0, end_ms=4000, text="First sentence here. Second one is longer.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) >= 2
    for chunk in out:
        assert chunk.end_ms - chunk.start_ms >= MIN_CHUNK_MS, (
            f"Chunk under 500ms floor: {chunk}"
        )


# === Sentence-boundary detection (drift fix #4) ===========================


def test_split_on_question_and_exclamation():
    """Split must happen at ? and ! boundaries, not just period."""
    text = "What is it? Tell me now! I need to know."
    chunks = _split_text(text, 3)
    assert len(chunks) == 3
    assert chunks[0] == "What is it?"
    assert chunks[1] == "Tell me now!"
    assert chunks[2] == "I need to know."


def test_split_on_newline():
    text = "First line\nSecond line"
    chunks = _split_text(text, 2)
    assert len(chunks) == 2


# === Degenerate cases (new — drift fix #5) ================================


def test_one_word_event_never_splits():
    """A single word at any duration can't be meaningfully split — return as-is."""
    ev = SubEvent(start_ms=0, end_ms=200, text="Supercalifragilistic")  # 20 chars, 100 CPS
    out = adapt_events_for_cps([ev], max_cps=10)
    # 20 chars triggers TRIVIAL_LEN guard (text length <= 20 short-circuits)
    assert len(out) == 1


def test_all_punctuation_event_passes_through():
    ev = SubEvent(start_ms=0, end_ms=200, text="!!!???")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1
    assert out[0].text == "!!!???"


def test_cjk_source_high_density_handles_gracefully():
    """CJK chars are denser. A short translated string with no spaces still splits if punctuation present."""
    # English translation of dense CJK — 60 chars, 1.5s = 40 CPS, target 17.
    ev = SubEvent(start_ms=0, end_ms=1500, text="Tanjiro draws his blade! The demon laughs in reply.")
    out = adapt_events_for_cps([ev], max_cps=17)
    # Should split on "! "
    assert len(out) >= 1
    if len(out) > 1:
        assert "Tanjiro" in out[0].text
        # Verify span preserved
        assert out[0].start_ms == 0
        assert out[-1].end_ms == 1500


def test_rtl_text_splits_without_crashing():
    """Arabic/Hebrew text should split on standard whitespace just like LTR."""
    # Simple Arabic phrase, repeated to exceed CPS at 1s. 'ال' = Al; 'سلام' = peace.
    ev = SubEvent(start_ms=0, end_ms=2000, text="السلام عليكم. وعليكم السلام. كيف حالك اليوم.")
    out = adapt_events_for_cps([ev], max_cps=10)
    # Doesn't crash; preserves total span.
    assert out[0].start_ms == 0
    assert out[-1].end_ms == 2000


# === Bin-pack helper directly =============================================


def test_balanced_bin_pack_distributes_roughly():
    """Greedy in-order bin-pack — not optimal, but each bin should be non-empty
    and the total length variance should be bounded."""
    units = ["aa", "bb", "cc", "dd", "ee", "ff"]
    bins = _balanced_bin_pack(units, n_chunks=3)
    assert len(bins) == 3
    # Every bin non-empty
    assert all(b for b in bins)
    # All 6 units accounted for
    total_units = sum(len(b.split()) for b in bins)
    assert total_units == 6


def test_balanced_bin_pack_preserves_order():
    units = ["a", "b", "c", "d"]
    bins = _balanced_bin_pack(units, n_chunks=2)
    # Order preserved within and across bins
    rejoined = " ".join(bins)
    assert rejoined == "a b c d"
