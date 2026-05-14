from server.subs.reading_rate import SubEvent, adapt_events_for_cps


def test_under_cps_passes_through():
    ev = SubEvent(start_ms=0, end_ms=3000, text="Short line.")
    out = adapt_events_for_cps([ev], max_cps=17)
    assert len(out) == 1
    assert out[0].text == "Short line."


def test_over_cps_splits_on_sentences():
    long_text = "This is a very long sentence. This is the second sentence."
    ev = SubEvent(start_ms=0, end_ms=2000, text=long_text)
    out = adapt_events_for_cps([ev], max_cps=15)
    assert len(out) >= 2


def test_split_preserves_duration_span():
    ev = SubEvent(start_ms=1000, end_ms=5000, text="Long line one. Long line two.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert out[0].start_ms == 1000
    assert out[-1].end_ms == 5000


def test_short_text_never_splits():
    ev = SubEvent(start_ms=0, end_ms=100, text="Hi.")
    out = adapt_events_for_cps([ev], max_cps=10)
    assert len(out) == 1
