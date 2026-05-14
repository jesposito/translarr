"""Reading-rate adapter.

Subtitle lines have a display duration. If a translated line is too long to read
within that duration (chars-per-second), we split it across multiple consecutive
subtitle events to keep CPS under the configured limit.
"""

from dataclasses import dataclass


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
    """Split any event whose translated text exceeds the CPS limit.

    Strategy: if a line is over CPS, split on sentence boundaries (or whitespace
    midpoint as fallback) and distribute duration proportionally.
    """
    out: list[SubEvent] = []
    for ev in events:
        if ev.cps <= max_cps or len(ev.text) <= 20:
            out.append(ev)
            continue
        chunks = _split_text(ev.text, target_cps=max_cps, duration_s=ev.duration_s)
        if len(chunks) == 1:
            out.append(ev)
            continue
        per_chunk_ms = (ev.end_ms - ev.start_ms) // len(chunks)
        for i, chunk in enumerate(chunks):
            start = ev.start_ms + i * per_chunk_ms
            end = ev.end_ms if i == len(chunks) - 1 else start + per_chunk_ms
            out.append(SubEvent(start_ms=start, end_ms=end, text=chunk, style=ev.style))
    return out


def _split_text(text: str, target_cps: int, duration_s: float) -> list[str]:
    max_chars = int(target_cps * duration_s)
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    for sentence in text.replace("\n", " ").split(". "):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            parts.append(sentence)
        else:
            parts.extend(_split_on_whitespace(sentence, max_chars))
    return parts or [text]


def _split_on_whitespace(text: str, max_chars: int) -> list[str]:
    words = text.split()
    out: list[str] = []
    buf: list[str] = []
    buflen = 0
    for w in words:
        if buflen + len(w) + 1 > max_chars and buf:
            out.append(" ".join(buf))
            buf, buflen = [w], len(w)
        else:
            buf.append(w)
            buflen += len(w) + 1
    if buf:
        out.append(" ".join(buf))
    return out
