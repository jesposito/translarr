"""Reading-rate adapter.

Subtitle lines have a display duration. If a translated line is too long to read
within that duration (chars-per-second), we split it across multiple consecutive
subtitle events to keep CPS under the configured limit.

Algorithm (per approved design doc + TR-7p7.1.9 + TR-7p7.1.10):

1. If `cps <= max_cps` OR the text is trivially short (<=20 chars): keep as-is.
2. Compute `n_chunks = ceil(len(text) / (max_cps * duration_s))` (minimum 2).
3. Tokenize text into ASS tags + words. ASS tags are atomic — never broken.
4. Try sentence-boundary split first (. / ? / ! / newline). If we get enough
   sentences, balance them across n_chunks bins.
5. Otherwise fall back to a COST-WEIGHTED token bin-pack:
     - Strong preference for splits after commas/semicolons/colons.
     - Preference for splits before conjunctions (and, but, when, ...) and
       prepositions (of, in, on, ...).
     - Penalty for splits after determiners (the/a/this/...) — these strand
       articles from their noun.
     - Penalty for splits before linking verbs (is/are/was/were/has/have/...) —
       these strand subject from verb.
   See TR-7p7.1.10.
6. Wrap each chunk with tag-close-and-reopen so paired ASS tags ({\\i1}...{\\i0},
   {\\b1}...{\\b0}, etc.) never fragment across chunks. See TR-7p7.1.9.
7. Distribute the original event duration proportionally to each chunk's
   *rendered* char-length share (tags excluded from the char count — they
   don't take reading time).
8. Enforce a 500ms minimum per chunk. If any chunk would be under 500ms, give
   up on the split and return the original event unchanged; log cps_overrun.
9. Last chunk's end_ms is pinned to the original end_ms so the total span is
   preserved exactly across split chunks.

No recursion. One pass per event.
"""

from __future__ import annotations

import itertools
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

# Matches a single ASS override block, e.g. {\i1}, {\fad(100,200)}, {\c&H00FF00&}.
_ASS_TAG_RE = re.compile(r"\{\\[^}]*\}")

# Paired ASS tags that need close-and-reopen treatment at split boundaries.
# Many ASS tags are stateful and continue until reset — these four are the
# most common pairs in subtitles. Less common: {\r} resets all, {\c&H...&}
# stays until next {\c} or {\r}. We don't try to balance those — they're rare
# in translated dialogue and the cost of getting them wrong is small.
TAG_OPEN_TO_CLOSE = {
    r"{\i1}": r"{\i0}",
    r"{\b1}": r"{\b0}",
    r"{\u1}": r"{\u0}",
    r"{\s1}": r"{\s0}",
}
TAG_CLOSE_TO_OPEN = {v: k for k, v in TAG_OPEN_TO_CLOSE.items()}
_OPEN_TAGS = set(TAG_OPEN_TO_CLOSE)
_CLOSE_TAGS = set(TAG_CLOSE_TO_OPEN)

# Linguistic phrase-boundary biases. See TR-7p7.1.10.
# Negative = preferred split (lower cost), positive = avoided split.
_PUNCT_BREAK_END = (",", ";", ":")
_CONJ_BEFORE = frozenset({
    "and", "but", "or", "so", "yet", "nor",
    "because", "if", "when", "while", "since", "though", "although",
    "before", "after", "until", "unless",
})
_PREP_BEFORE = frozenset({
    "of", "in", "on", "at", "to", "with", "by", "from", "for",
    "into", "onto", "about", "through", "across", "between", "among",
})
_DETERMINER = frozenset({"the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our", "their"})
_LINKING_VERB = frozenset({
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "does", "did", "do",
})


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


# ===== Public API =========================================================


def adapt_events_for_cps(events: list[SubEvent], max_cps: int) -> list[SubEvent]:
    """Adapt overlong events per the algorithm above. Pure function — input untouched."""
    out: list[SubEvent] = []
    for ev in events:
        out.extend(_adapt_one(ev, max_cps))
    return out


# ===== Per-event adaptation ===============================================


def _adapt_one(ev: SubEvent, max_cps: int) -> list[SubEvent]:
    # Step 1: short-circuit on under-threshold or trivially-short events.
    if ev.cps <= max_cps or len(ev.text) <= TRIVIAL_LEN:
        return [ev]

    # Step 2: choose chunk count.
    duration_s = ev.duration_s
    text_len = len(ev.text)
    n_chunks = max(2, math.ceil(text_len / (max_cps * duration_s)))

    # Step 3+4: split text into n_chunks parts (tag-balanced + linguistically aware).
    chunks = _split_text(ev.text, n_chunks)
    if len(chunks) < 2:
        return [ev]

    # Step 7: proportional duration allocation using *rendered* chars (tags excluded).
    rendered_lens = [_rendered_char_count(c) for c in chunks]
    total_rendered = sum(rendered_lens)
    if total_rendered == 0:
        return [ev]

    total_ms = ev.end_ms - ev.start_ms
    planned: list[tuple[int, int, str]] = []
    cursor = ev.start_ms
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            end = ev.end_ms
        else:
            portion = rendered_lens[i] / total_rendered
            chunk_ms = int(total_ms * portion)
            end = cursor + chunk_ms
        planned.append((cursor, end, chunk))
        cursor = end

    # Step 8: enforce 500ms floor — bail if any chunk would be too short.
    for start, end, _chunk in planned:
        if end - start < MIN_CHUNK_MS:
            log.info(
                "cps_overrun_keep_original "
                "start_ms=%d end_ms=%d cps=%.1f text_len=%d n_chunks=%d",
                ev.start_ms, ev.end_ms, ev.cps, text_len, n_chunks,
            )
            return [ev]

    return [SubEvent(start_ms=s, end_ms=e, text=t, style=ev.style) for s, e, t in planned]


# ===== Text splitting =====================================================


def _split_text(text: str, n_chunks: int) -> list[str]:
    """Split `text` into roughly `n_chunks` parts, then balance ASS tag pairs.

    Layer 1 (preferred): sentence boundaries (./?/! followed by whitespace, or newline).
                         If we have >= n_chunks sentences, pack them into n_chunks bins.
    Layer 2 (fallback): cost-weighted token bin-pack on words (preserving ASS tag atomicity).
    """
    text = text.strip()
    if not text:
        return [text]

    # Layer 1.
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(text) if s.strip()]
    if len(sentences) >= n_chunks:
        joined_chunks = _balanced_bin_pack(sentences, n_chunks)
        return _balance_tag_pairs_across_chunks(joined_chunks)

    # Layer 2: weighted bin-pack on token-level (so ASS tags stay atomic).
    tokens = _tokenize_with_tags(text)
    word_count = sum(1 for t in tokens if not _ASS_TAG_RE.fullmatch(t))
    if word_count < n_chunks:
        return [text]
    chunks = _weighted_token_bin_pack(tokens, n_chunks)
    return _balance_tag_pairs_across_chunks(chunks)


def _tokenize_with_tags(text: str) -> list[str]:
    """Split text into tokens. ASS tags (e.g. {\\i1}) are atomic single tokens;
    everything else splits on whitespace."""
    out: list[str] = []
    cursor = 0
    for m in _ASS_TAG_RE.finditer(text):
        before = text[cursor:m.start()]
        if before:
            out.extend(before.split())
        out.append(m.group(0))
        cursor = m.end()
    tail = text[cursor:]
    if tail:
        out.extend(tail.split())
    return out


def _rendered_char_count(chunk: str) -> int:
    """Char count excluding ASS tag overhead — used for reading-time proportions."""
    return len(_ASS_TAG_RE.sub("", chunk))


# ===== Cost-weighted token bin-pack =======================================


def _split_cost(left_token: str, right_token: str) -> float:
    """Cost of splitting AFTER left_token (so right_token starts the next chunk).

    Lower = better. 0.0 is neutral default. Negative = preferred. Positive = avoid.
    """
    cost = 0.0
    left_lower = left_token.lower()
    right_lower = right_token.lower()

    # Punctuation at end of left token: strongest break signal.
    if left_token.rstrip().endswith(_PUNCT_BREAK_END):
        cost -= 2.0

    # Right token is a conjunction (and/but/when/...): preferred break.
    if right_lower in _CONJ_BEFORE:
        cost -= 1.5

    # Right token is a preposition: mild preferred.
    if right_lower in _PREP_BEFORE:
        cost -= 1.0

    # Left token is a determiner (the/a/this/...): bad — strands article from noun.
    if left_lower in _DETERMINER:
        cost += 2.0

    # Right token is a linking verb (is/are/was/...): bad — strands subject from verb.
    if right_lower in _LINKING_VERB:
        cost += 1.5

    return cost


def _weighted_token_bin_pack(tokens: list[str], n_chunks: int) -> list[str]:
    """Distribute `tokens` across `n_chunks` bins, biased by linguistic boundaries.

    Strategy: find the (n_chunks - 1) best (lowest-cost AND char-length-balanced)
    gaps between WORD tokens. ASS tag tokens are never split-after — they get
    bundled with their following word.

    Returns the joined chunks (space-joined, with ASS tags placed inline).
    """
    # Identify candidate split positions: gaps that aren't between two ASS tags
    # or directly after an ASS tag (you shouldn't split after an open tag).
    candidates: list[tuple[int, float, int]] = []  # (after_idx, cost, char_offset)
    char_offset = 0
    for i, tok in enumerate(tokens):
        char_offset += len(tok) + (1 if i < len(tokens) - 1 else 0)
        if i == len(tokens) - 1:
            continue
        left = tok
        right = tokens[i + 1]
        # Don't split immediately after an open tag — that strands the tag.
        if left in _OPEN_TAGS:
            continue
        # Don't split immediately before a close tag — that strands the close.
        if right in _CLOSE_TAGS:
            continue
        # Don't split between two tags — meaningless.
        if _ASS_TAG_RE.fullmatch(left) and _ASS_TAG_RE.fullmatch(right):
            continue
        cost = _split_cost(left, right)
        candidates.append((i, cost, char_offset))

    if len(candidates) < n_chunks - 1:
        # Not enough candidate gaps. Fall back to naive even split.
        return _balanced_bin_pack(tokens, n_chunks)

    # Pick n_chunks-1 split points. Each ideal split is near the boundary of an
    # equal char-share of the total. Score each candidate by cost + |distance from
    # ideal boundary|. Greedy: for each of n_chunks-1 target offsets, pick the
    # candidate that minimizes (cost + distance_penalty).
    total_chars = char_offset
    chosen: list[int] = []  # token indices to split AFTER
    used_idxs: set[int] = set()
    for k in range(1, n_chunks):
        target = total_chars * (k / n_chunks)
        best = None
        best_score = float("inf")
        for after_idx, cost, offset in candidates:
            if after_idx in used_idxs:
                continue
            # Distance penalty: 1 unit per 5 chars off target.
            dist_pen = abs(offset - target) / 5.0
            score = cost + dist_pen
            if score < best_score:
                best_score = score
                best = after_idx
        if best is None:
            break
        chosen.append(best)
        used_idxs.add(best)

    chosen.sort()
    # Build chunks.
    chunks: list[str] = []
    prev = -1
    for cut in chosen:
        chunks.append(_join_tokens(tokens[prev + 1 : cut + 1]))
        prev = cut
    chunks.append(_join_tokens(tokens[prev + 1 :]))
    return [c for c in chunks if c]


def _join_tokens(tokens: list[str]) -> str:
    """Join tokens with spaces, with surgical exceptions for ASS tag adjacency:

    - Open tag attaches to the FOLLOWING word with no space: `{\\i1}world`
      (the tag styles what comes after, never has visible space).
    - Close tag attaches to the PRECEDING word with no space: `world{\\i0}`
      (the tag closes what came before).
    - Everything else gets a normal space.

    This preserves the original spacing semantics — both
    `Hello {\\i1}world{\\i0} friend` and `world{\\i0}from` are produced
    correctly.
    """
    if not tokens:
        return ""
    out = [tokens[0]]
    for prev, cur in itertools.pairwise(tokens):
        # No space if prev is an open tag (tag adheres to following content)
        # OR current is a close tag (tag adheres to preceding content).
        if prev in _OPEN_TAGS or cur in _CLOSE_TAGS:
            out.append(cur)
        else:
            out.append(" " + cur)
    return "".join(out)


# ===== Sentence-level bin-pack (Layer 1) ===================================


def _balanced_bin_pack(units: list[str], n_chunks: int, joiner: str = " ") -> list[str]:
    """Greedy in-order bin-pack on sentences (Layer 1 use only).

    Walks units in order; advances to next bin when current bin has exceeded
    its fair share of remaining work.
    """
    bins: list[list[str]] = [[] for _ in range(n_chunks)]
    bin_lens = [0] * n_chunks
    remaining_chars = sum(len(u) + len(joiner) for u in units)
    bins_remaining = n_chunks
    idx = 0
    for u in units:
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
    return [joiner.join(b) for b in bins if b]


# ===== ASS tag pair balancing (TR-7p7.1.9) ================================


def _balance_tag_pairs_across_chunks(chunks: list[str]) -> list[str]:
    """For each chunk, close any tags still open at its end; reopen them at start of next chunk.

    Walks chunks left-to-right, tracking the open-tag stack. For chunk N's
    accumulated open tags that aren't closed by end of chunk N, we:
      - Append matching close tags to end of chunk N
      - Prepend matching open tags to start of chunk N+1

    This ensures each chunk is self-contained ASS so renderers that reset state
    per cue (most of them, per ASS spec) display each chunk correctly.

    Handles only paired tags in TAG_OPEN_TO_CLOSE. Unpaired tags (color, font,
    fade) pass through; they're rare in dialogue and the cost of leaving them
    fragmented is small.
    """
    if not chunks:
        return chunks
    out: list[str] = []
    carry_over_opens: list[str] = []  # Tags to prepend to the next chunk
    for chunk in chunks:
        # Prepend any tags inherited from the prior chunk.
        prefixed = "".join(carry_over_opens) + chunk

        # Walk the prefixed chunk and track open/close stack.
        stack: list[str] = []
        for m in _ASS_TAG_RE.finditer(prefixed):
            tag = m.group(0)
            if tag in _OPEN_TAGS:
                stack.append(tag)
            elif tag in _CLOSE_TAGS:
                matching_open = TAG_CLOSE_TO_OPEN[tag]
                # Pop the most recent matching open (well-formed ASS expected).
                for j in range(len(stack) - 1, -1, -1):
                    if stack[j] == matching_open:
                        stack.pop(j)
                        break

        # Tags still open at end of chunk -> close them here, reopen next chunk.
        closes_for_this_chunk = "".join(TAG_OPEN_TO_CLOSE[t] for t in reversed(stack))
        # Strip trailing whitespace before appending close tags so they hug
        # the preceding text (per ASS-tag adjacency convention).
        out.append(prefixed.rstrip() + closes_for_this_chunk)
        carry_over_opens = list(stack)
    return out
