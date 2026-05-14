"""Reading-rate adapter.

Subtitle lines have a display duration. If a translated line is too long to read
within that duration (chars-per-second), we split it across multiple consecutive
subtitle events to keep CPS under the configured limit.

Algorithm (matches design doc):

1. If `cps <= max_cps` OR the text is trivially short (<=20 chars): keep as-is.
2. Compute `n_chunks = ceil(len(text) / (max_cps * duration_s))` (minimum 2).
3. Split text into `n_chunks` parts. Prefer sentence boundaries (`. `, `? `, `! `,
   newline); fall back to greedy whitespace bin-pack on words.
4. Distribute the original event duration **proportionally** to each chunk's
   char-length share — long chunks get more reading time than short ones.
5. Enforce a 500ms minimum per chunk. If any chunk would be under 500ms, **give
   up** on the split and return the original event unchanged; log a `cps_overrun`
   event for telemetry.
6. Last chunk's `end_ms` is pinned to the original `end_ms` so the total span is
   preserved exactly across the split chunks.

No recursion. One pass per event.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Minimum chunk duration. Below this, a sub event is unreadable regardless of
# CPS — better to live with a CPS overrun than flash subs.
MIN_CHUNK_MS = 500

# Trivial-line guard: don't bother splitting strings this short even if CPS is high.
TRIVIAL_LEN = 20

# Sentence-boundary regex: split AFTER `.`/`?`/`!` followed by whitespace, OR on a newline.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.?!])\s+|\n")


@dataclass
class SubEvent:
    start_ms: int
    end_ms: int
    text: str
    style: str | None = None

    @property
    def duration_s(self) -> float:
        return max((self.end_ms - self.start_ms) / 1000.0, 0.001)

    @property
    def cps(self) -> float:
        return len(self.text.replace("\n", "")) / self.duration_s


def adapt_events_for_cps(events: list[SubEvent], max_cps: int) -> list[SubEvent]:
    """Adapt overlong events per the algorithm above. Pure function — input untouched."""
    out: list[SubEvent] = []
    for ev in events:
        out.extend(_adapt_one(ev, max_cps))
    return out


def _adapt_one(ev: SubEvent, max_cps: int) -> list[SubEvent]:
    # Step 1: short-circuit on under-threshold or trivially-short events.
    if ev.cps <= max_cps or len(ev.text) <= TRIVIAL_LEN:
        return [ev]

    # Step 2: choose chunk count.
    duration_s = ev.duration_s
    text_len = len(ev.text)
    n_chunks = max(2, math.ceil(text_len / (max_cps * duration_s)))

    # Step 3: split text.
    chunks = _split_text(ev.text, n_chunks)
    if len(chunks) < 2:
        return [ev]

    # Step 4: proportional duration allocation.
    total_chars = sum(len(c) for c in chunks)
    if total_chars == 0:
        return [ev]

    total_ms = ev.end_ms - ev.start_ms
    planned: list[tuple[int, int, str]] = []  # (start_ms, end_ms, text)
    cursor = ev.start_ms
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            # Last chunk pins end to the original end_ms — no drift from rounding.
            end = ev.end_ms
        else:
            portion = len(chunk) / total_chars
            chunk_ms = int(total_ms * portion)
            end = cursor + chunk_ms
        planned.append((cursor, end, chunk))
        cursor = end

    # Step 5: enforce 500ms floor — bail if any chunk would be too short.
    for start, end, _chunk in planned:
        if end - start < MIN_CHUNK_MS:
            log.info(
                "cps_overrun_keep_original "
                "start_ms=%d end_ms=%d cps=%.1f text_len=%d n_chunks=%d",
                ev.start_ms, ev.end_ms, ev.cps, text_len, n_chunks,
            )
            return [ev]

    return [SubEvent(start_ms=s, end_ms=e, text=t, style=ev.style) for s, e, t in planned]


def _split_text(text: str, n_chunks: int) -> list[str]:
    """Split `text` into roughly `n_chunks` parts.

    Layer 1: sentence boundaries (./?/! followed by whitespace, or newline).
             If there are at least `n_chunks` sentences, greedy bin-pack them
             into n_chunks balanced buckets.
    Layer 2: greedy whitespace bin-pack on words.
    """
    text = text.strip()
    if not text:
        return [text]

    # Layer 1: try sentence boundaries.
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(text) if s.strip()]
    if len(sentences) >= n_chunks:
        return _balanced_bin_pack(sentences, n_chunks)

    # Layer 2: greedy whitespace bin-pack on words.
    words = text.split()
    if len(words) < n_chunks:
        # Can't make n_chunks chunks without splitting words; bail.
        return [text]
    return _balanced_bin_pack(words, n_chunks, joiner=" ")


def _balanced_bin_pack(units: list[str], n_chunks: int, joiner: str = " ") -> list[str]:
    """Distribute `units` across exactly `n_chunks` bins minimizing per-bin char-count variance.

    Greedy: at each step, assign the next unit to the bin with the smallest current length.
    Preserves order within bins (necessary for subtitles).
    """
    bins: list[list[str]] = [[] for _ in range(n_chunks)]
    bin_lens = [0] * n_chunks
    # We need to preserve unit order, so we can't just assign-to-smallest-bin globally.
    # Instead, walk units in order and fill bin[i] until it's "fair share" of remaining work.
    remaining_chars = sum(len(u) + len(joiner) for u in units)
    bins_remaining = n_chunks
    idx = 0
    for u in units:
        # Allow current bin to overshoot fair share slightly; advance when it's reached its target.
        target = remaining_chars / bins_remaining if bins_remaining > 0 else float("inf")
        if (
            idx < n_chunks - 1
            and bin_lens[idx] > 0
            and bin_lens[idx] + len(u) + len(joiner) > target * 1.3
        ):
            idx += 1
            bins_remaining -= 1
        bins[idx].append(u)
        bin_lens[idx] += len(u) + len(joiner)
        remaining_chars -= len(u) + len(joiner)

    # Drop empty bins (shouldn't happen if len(units) >= n_chunks, but defensive).
    return [joiner.join(b) for b in bins if b]
