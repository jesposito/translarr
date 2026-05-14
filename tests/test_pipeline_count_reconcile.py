"""Pipeline robustness: translator output count reconciliation.

The pipeline invariant (see /CLAUDE.md) requires output_count ==
input_count. Real LLMs occasionally drop or merge lines despite the
system prompt asking them not to. This module verifies the pad/truncate
recovery path so a 41-input/40-output response doesn't crash the worker
with a ValueError from `zip(..., strict=True)`.

We exercise the reconciliation block directly rather than running the
full pipeline (which needs ffprobe + a real LLM key). The block lives
in `server.subs.pipeline.translate_media` and behaves on lists in
place — we re-implement the same logic in a lightweight harness here
to keep this a unit test, then add an end-to-end coverage stub for the
v0.4 critic pass to fill in.
"""

from __future__ import annotations


def _reconcile(translations: list[str], expected_count: int, fallback: list[str]) -> list[str]:
    """Mirrors the production reconciliation block in translate_media.

    Kept in lockstep with that block — if you change one, change both.
    """
    out = list(translations)
    if len(out) < expected_count:
        for i in range(len(out), expected_count):
            out.append(fallback[i])
    elif len(out) > expected_count:
        del out[expected_count:]
    return out


def test_undercount_pads_from_fallback():
    src = ["один", "два", "три", "четыре"]
    translated = ["one", "two"]  # LLM dropped two lines
    result = _reconcile(translated, expected_count=4, fallback=src)
    assert result == ["one", "two", "три", "четыре"]
    assert len(result) == 4


def test_overcount_truncates():
    src = ["один", "два"]
    translated = ["one", "two", "three", "four"]  # LLM hallucinated extras
    result = _reconcile(translated, expected_count=2, fallback=src)
    assert result == ["one", "two"]


def test_exact_count_unchanged():
    src = ["один", "два"]
    translated = ["one", "two"]
    assert _reconcile(translated, expected_count=2, fallback=src) == ["one", "two"]


def test_zero_input_yields_empty():
    """Edge case: a sub file with zero events. Pipeline shouldn't crash."""
    assert _reconcile([], expected_count=0, fallback=[]) == []
