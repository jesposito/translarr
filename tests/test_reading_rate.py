"""Reading-rate adapter tests.

Covers v0.1 baseline + the TR-7p7.1.8 (algorithm drift), TR-7p7.1.9 (tag-pair
fragmentation), and TR-7p7.1.10 (linguistic split-point quality) fixes.
"""

from server.subs.reading_rate import (
    _ASS_TAG_RE,
    MIN_CHUNK_MS,
    SubEvent,
    _balance_tag_pairs_across_chunks,
    _balanced_bin_pack,
    _join_tokens,
    _rendered_char_count,
    _split_cost,
    _split_text,
    _tokenize_with_tags,
    _weighted_token_bin_pack,
    adapt_events_for_cps,
)

# === v0.1 baseline preserved ==============================================


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


# === Proportional duration (TR-7p7.1.8 fix #1) ============================


def test_proportional_duration_allocation():
    ev = SubEvent(
        start_ms=0,
        end_ms=4000,
        text="Tiny. A much longer sentence that needs way more reading time than the tiny one before it.",
    )
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) >= 2
    out_sorted = sorted(out, key=lambda c: c.start_ms)
    first_dur = out_sorted[0].end_ms - out_sorted[0].start_ms
    last_dur = out_sorted[-1].end_ms - out_sorted[-1].start_ms
    assert last_dur > first_dur


# === 500ms floor (TR-7p7.1.8 fix #2) ======================================


def test_500ms_floor_keeps_original_when_split_would_be_too_short():
    ev = SubEvent(
        start_ms=0, end_ms=600,
        text="This is way too much text for six hundred milliseconds period.",
    )
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1


def test_500ms_floor_allows_split_when_each_chunk_clears_floor():
    ev = SubEvent(start_ms=0, end_ms=4000, text="First sentence here. Second one is longer.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) >= 2
    for chunk in out:
        assert chunk.end_ms - chunk.start_ms >= MIN_CHUNK_MS


# === Sentence-boundary detection (TR-7p7.1.8 fix #4) ======================


def test_split_on_question_and_exclamation():
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


# === Degenerate cases =====================================================


def test_one_word_event_never_splits():
    ev = SubEvent(start_ms=0, end_ms=200, text="Supercalifragilistic")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1


def test_all_punctuation_event_passes_through():
    ev = SubEvent(start_ms=0, end_ms=200, text="!!!???")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1


def test_rtl_text_splits_without_crashing():
    ev = SubEvent(start_ms=0, end_ms=2000, text="السلام عليكم. وعليكم السلام. كيف حالك اليوم.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert out[0].start_ms == 0
    assert out[-1].end_ms == 2000


# === Tokenizer (TR-7p7.1.9 prereq) ========================================


def test_tokenize_preserves_ass_tags_atomically():
    text = r"{\i1}Hello there{\i0} friend"
    tokens = _tokenize_with_tags(text)
    assert r"{\i1}" in tokens
    assert r"{\i0}" in tokens
    assert "Hello" in tokens
    assert "there" in tokens
    assert "friend" in tokens


def test_tokenize_complex_tag_inside_text():
    text = r"The {\fad(100,200)}fade is here"
    tokens = _tokenize_with_tags(text)
    assert r"{\fad(100,200)}" in tokens


def test_rendered_char_count_excludes_tags():
    """Tags don't count toward reading time."""
    text_with_tags = r"{\i1}Hello{\i0}"
    text_plain = "Hello"
    assert _rendered_char_count(text_with_tags) == _rendered_char_count(text_plain) == 5


def test_join_tokens_no_space_between_tag_and_word():
    tokens = [r"{\i1}", "Hello", "world", r"{\i0}"]
    assert _join_tokens(tokens) == r"{\i1}Hello world{\i0}"


# === Tag-pair balancing (TR-7p7.1.9) ======================================


def test_tag_pair_balanced_when_split_in_middle():
    """The bug from the Demon Slayer A/B:
    {\\i1}Insect Breathing,| Ultimate Strike{\\i0} from Behind: should auto-close + reopen."""
    chunks = [r"{\i1}Insect Breathing,", r"Ultimate Strike{\i0} from Behind:"]
    balanced = _balance_tag_pairs_across_chunks(chunks)
    # Chunk 1: italic must close before end of chunk
    assert balanced[0].endswith(r"{\i0}"), f"Chunk 1 should auto-close italic: {balanced[0]!r}"
    # Chunk 2: italic must reopen at start of chunk
    assert balanced[1].startswith(r"{\i1}"), f"Chunk 2 should auto-reopen italic: {balanced[1]!r}"
    # The original close tag stays where it was — no drop
    assert r"{\i0}" in balanced[1]


def test_tag_pair_not_modified_when_already_balanced():
    chunks = [r"{\i1}Hello{\i0}", "world"]
    balanced = _balance_tag_pairs_across_chunks(chunks)
    assert balanced[0] == r"{\i1}Hello{\i0}"
    assert balanced[1] == "world"


def test_multiple_nested_tags_balanced():
    """Bold + italic open, then both close in next chunk."""
    chunks = [r"{\i1}{\b1}Hello", r"world{\b0}{\i0}"]
    balanced = _balance_tag_pairs_across_chunks(chunks)
    # Chunk 1 must close both before end (in reverse-open order: b0 then i0).
    # We don't care which exact order — just that no chunk has unmatched opens.
    open_count_1 = sum(1 for _ in _ASS_TAG_RE.finditer(balanced[0]) if _.group(0) in {r"{\i1}", r"{\b1}"})
    close_count_1 = sum(1 for _ in _ASS_TAG_RE.finditer(balanced[0]) if _.group(0) in {r"{\i0}", r"{\b0}"})
    assert open_count_1 == close_count_1, f"Chunk 1 has unbalanced tags: {balanced[0]!r}"
    open_count_2 = sum(1 for _ in _ASS_TAG_RE.finditer(balanced[1]) if _.group(0) in {r"{\i1}", r"{\b1}"})
    close_count_2 = sum(1 for _ in _ASS_TAG_RE.finditer(balanced[1]) if _.group(0) in {r"{\i0}", r"{\b0}"})
    assert open_count_2 == close_count_2, f"Chunk 2 has unbalanced tags: {balanced[1]!r}"


def test_tag_pair_balanced_through_full_pipeline():
    """End-to-end: an event with paired tags that splits should produce two
    chunks each with balanced tag pairs."""
    # 4-second duration with a 78-char line forces a 2-chunk split; both
    # chunks land >500ms so the floor doesn't bail.
    ev = SubEvent(
        start_ms=0,
        end_ms=4000,
        text=r"{\i1}Insect Breathing, Ultimate Strike{\i0} from Behind: now and forever amen!",
    )
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) >= 2, f"expected split, got: {[c.text for c in out]}"
    for chunk in out:
        opens = sum(1 for m in _ASS_TAG_RE.finditer(chunk.text) if m.group(0) == r"{\i1}")
        closes = sum(1 for m in _ASS_TAG_RE.finditer(chunk.text) if m.group(0) == r"{\i0}")
        assert opens == closes, f"Chunk has unbalanced italic: {chunk.text!r}"


# === Linguistic split-point quality (TR-7p7.1.10) =========================


def test_split_cost_prefers_after_comma():
    """Splitting after a comma should be cheaper than splitting between two random words."""
    after_comma = _split_cost("hurry,", "the")
    middle = _split_cost("hurry", "demon")
    assert after_comma < middle


def test_split_cost_avoids_determiner_noun():
    """The/a/this followed by noun is a bad split point."""
    determiner_split = _split_cost("the", "demon")
    neutral_split = _split_cost("hurry", "demon")
    assert determiner_split > neutral_split


def test_split_cost_avoids_subject_verb():
    """X is/are/was should be avoided as a split point — strands subject from verb."""
    subj_verb = _split_cost("muscles", "are")
    neutral = _split_cost("muscles", "trembling")
    assert subj_verb > neutral


def test_split_cost_prefers_before_conjunction():
    """X | but Y is a preferred split."""
    before_conj = _split_cost("home", "but")
    neutral = _split_cost("home", "today")
    assert before_conj < neutral


def test_weighted_bin_pack_chooses_comma_split():
    """Given 'Tanjiro draws his blade, the demon laughs in reply' split in 2 ->
    should split after the comma."""
    tokens = ["Tanjiro", "draws", "his", "blade,", "the", "demon", "laughs", "in", "reply"]
    chunks = _weighted_token_bin_pack(tokens, n_chunks=2)
    assert len(chunks) == 2
    assert "blade," in chunks[0]
    assert chunks[1].startswith("the")


def test_weighted_bin_pack_avoids_splitting_after_determiner():
    """Should NOT produce 'the | demon laughs ...' as the split."""
    # 'A | very long sentence about the topic' — should avoid splitting after 'A'.
    tokens = ["A", "very", "long", "sentence", "about", "a", "particular", "topic", "appears", "here"]
    chunks = _weighted_token_bin_pack(tokens, n_chunks=2)
    # First chunk should not end with a determiner
    assert chunks[0].split()[-1].lower() not in {"a", "the", "an"}, (
        f"Bin-pack ended chunk with determiner: {chunks[0]!r}"
    )


def test_weighted_bin_pack_avoids_subject_verb_split():
    """'My muscles are trembling fiercely now' split in 2 should NOT produce
    'My muscles' / 'are trembling fiercely now' (strands subject from verb)."""
    tokens = ["My", "muscles", "are", "trembling", "fiercely", "now", "indeed"]
    chunks = _weighted_token_bin_pack(tokens, n_chunks=2)
    # First chunk should not end on a noun-likely word that precedes 'are'.
    # The fix isn't perfect (we don't actually parse), but verify the cost
    # function pushed it elsewhere.
    assert not chunks[0].rstrip().endswith("muscles"), (
        f"Bin-pack split between subject and verb: {chunks!r}"
    )


# === Bin-pack helper =======================================================


def test_balanced_bin_pack_distributes_roughly():
    units = ["aa", "bb", "cc", "dd", "ee", "ff"]
    bins = _balanced_bin_pack(units, n_chunks=3)
    assert len(bins) == 3
    assert all(b for b in bins)
    total_units = sum(len(b.split()) for b in bins)
    assert total_units == 6


def test_balanced_bin_pack_preserves_order():
    units = ["a", "b", "c", "d"]
    bins = _balanced_bin_pack(units, n_chunks=2)
    rejoined = " ".join(bins)
    assert rejoined == "a b c d"
