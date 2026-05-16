"""Series / film candidate enumeration for the glossary picker.

Users shouldn't have to invent a "glossary ID" out of thin air — the
glossary page asks them to pick a series or film from their actual
media library. This module walks ``MEDIA_ROOT`` looking for
"series-like" directories (one level deep, then two for the common
Movies/X, TV/Y layout) and supports server-side substring filtering.

Scale notes
-----------
A real library has hundreds to thousands of titles. Walking the FS on
every keystroke would be wasteful, so results are cached in process
for ``SCAN_TTL_SECONDS``. The cache is invalidated by time, not events.
New releases show up within a minute, which is fine for a glossary
picker. Substring filter runs against the cached list (O(N) over a few
thousand items is sub-millisecond).

Annotation
----------
Each returned candidate carries `glossary_entry_count` and
`has_series_config` so the picker can show "12 entries" vs "no
glossary yet" beside each title.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from server.config import settings

log = structlog.get_logger()

# How long the FS scan stays cached. 60s strikes a balance — long enough
# that a typing user doesn't trigger 20 walks/second, short enough that
# a new release showing up in the library appears quickly.
SCAN_TTL_SECONDS = 60

# Hard cap on the cached scan to keep memory bounded under pathological
# libraries (5000+ movies). The user's actual library has ~500.
MAX_CANDIDATES = 10_000

# Names we never treat as candidates.
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".git", "@eaDir", "Metadata", "Recordings"}
SKIP_PREFIXES = ("._", ".")


@dataclass
class Candidate:
    """One series / film discoverable in the library."""

    name: str          # Display name — the directory basename.
    path: str          # Path relative to MEDIA_ROOT (forward slashes).
    kind: str          # Parent category, e.g. "Movies", "TV". Empty for loose layout.


_cache: dict = {"at": 0.0, "items": []}


def _slugify(name: str) -> str:
    """Lowercase, hyphenate. Matches the slug the Library page produces."""
    import re

    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


def _scan_library() -> list[Candidate]:
    """Walk MEDIA_ROOT looking for series-like directories.

    Strategy:
    - For every top-level subdir of MEDIA_ROOT (Movies/, TV/, Anime/, ...),
      list its immediate children. Those are the "series / film" candidates.
    - If a top-level subdir has no subdirs of its own (it's a flat layout
      with bare media files), the top dir IS the candidate.

    Returns at most MAX_CANDIDATES entries. Walk is shallow — never
    deeper than two levels from MEDIA_ROOT.
    """
    root = settings.media_root.resolve()
    items: list[Candidate] = []
    try:
        top_level = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except (FileNotFoundError, PermissionError):
        return items

    for top in top_level:
        if not top.is_dir() or _should_skip(top.name):
            continue
        try:
            child_dirs = [
                c for c in top.iterdir()
                if c.is_dir() and not _should_skip(c.name)
            ]
        except (PermissionError, OSError):
            continue

        if child_dirs:
            # Common layout: Movies/Foo, TV/Bar — child dirs are candidates.
            child_dirs.sort(key=lambda p: p.name.lower())
            for child in child_dirs:
                items.append(Candidate(
                    name=child.name,
                    path=str(child.relative_to(root)),
                    kind=top.name,
                ))
                if len(items) >= MAX_CANDIDATES:
                    return items
        else:
            # Loose layout: top dir is the candidate.
            items.append(Candidate(name=top.name, path=top.name, kind=""))
            if len(items) >= MAX_CANDIDATES:
                return items
    return items


def _should_skip(name: str) -> bool:
    if name in SKIP_NAMES:
        return True
    return any(name.startswith(p) for p in SKIP_PREFIXES)


def _get_cached() -> list[Candidate]:
    now = time.monotonic()
    if now - _cache["at"] > SCAN_TTL_SECONDS or not _cache["items"]:
        _cache["items"] = _scan_library()
        _cache["at"] = now
        log.info("candidates_scan", count=len(_cache["items"]))
    return _cache["items"]


def invalidate_cache() -> None:
    """Force the next call to rescan. Test-only helper."""
    _cache["at"] = 0.0
    _cache["items"] = []


def search_candidates(query: str, limit: int = 50) -> list[dict]:
    """Filter the cached candidate list by case-insensitive substring.

    Ranking: directories whose name *starts with* the query rank ahead
    of those that merely *contain* it. Within each tier, alphabetical.
    Returns plain dicts (not Candidate dataclasses) so the result is
    JSON-serializable. Each result is annotated with glossary +
    series_config status so the picker can show "12 entries" beside the
    matching title.
    """
    from server.glossary import list_glossaries
    from server.series_config import list_series

    all_candidates = _get_cached()
    q = (query or "").strip().lower()

    if not q:
        chosen = all_candidates[:limit]
    else:
        starts: list[Candidate] = []
        contains: list[Candidate] = []
        for c in all_candidates:
            name_lower = c.name.lower()
            if name_lower.startswith(q):
                starts.append(c)
            elif q in name_lower:
                contains.append(c)
            if len(starts) + len(contains) >= limit * 4:
                # Stop scanning once we've collected enough headroom.
                break
        chosen = (starts + contains)[:limit]

    # Build lookup tables once per call so annotation is O(1) per result.
    glossary_index = {g["id"]: g for g in list_glossaries()}
    series_ids = {s["id"] for s in list_series()}

    annotated: list[dict] = []
    for c in chosen:
        slug = _slugify(c.name)
        g = glossary_index.get(slug)
        annotated.append({
            "name": c.name,
            "path": c.path,
            "kind": c.kind,
            "glossary_id": slug,
            "glossary_entry_count": g["entry_count"] if g else 0,
            "has_series_config": slug in series_ids,
        })
    return annotated
