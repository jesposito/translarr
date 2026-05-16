import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pysubs2
import structlog

from server import cost_tracker, library_refresh
from server.async_utils import fire_and_forget
from server.config import settings
from server.llm.router import get_provider
from server.models import TranslateRequest, TranslateResponse
from server.subs.extract import extract_track, list_sub_tracks, pick_source_track
from server.subs.reading_rate import SubEvent, adapt_events_for_cps

log = structlog.get_logger()


SUB_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}


class AlreadyTranslated(Exception):
    """Raised when output file exists and force=false. Carries existing path."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"already_translated: {path}")
        self.path = path


class NoSourceSubtitles(Exception):
    """Raised when no embedded sub tracks AND no usable sidecar file exist.

    Treated as a terminal skip by the queue worker — NOT a retry-eligible
    failure. Retrying would re-run ffprobe + sidecar scan with the same
    inputs and waste worker cycles.

    The playback.start webhook path uses this signal to mark on-demand
    triggers as $0 skips when there is genuinely nothing to translate
    (e.g. user pressed Play on a file that already has only English subs).
    """


def _output_path(media: Path, target_lang: str) -> Path:
    """Translarr output filename: <basename>.<lang>.translarr.srt next to source media.

    The `.translarr.` infix avoids colliding with `<basename>.<lang>.srt` which is
    human/Bazarr/embedded-extraction territory.
    """
    base = media.stem
    return media.parent / f"{base}.{target_lang}.translarr.srt"


def _backup_existing(path: Path) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(f".{ts}.bak.srt")
    path.rename(backup)
    return backup


async def translate_media(req: TranslateRequest) -> TranslateResponse:
    media = settings.media_root / req.media_path if not req.media_path.is_absolute() else req.media_path
    if not media.exists():
        raise FileNotFoundError(f"media not found: {media}")

    target_lang = req.target_lang or settings.target_lang
    source_lang = req.source_lang or "auto"
    detected_track_index: int | None = req.source_track_index

    # Output-collision policy (v0.1) — checked before any expensive work.
    out_path = _output_path(media, target_lang)
    if out_path.exists():
        if not getattr(req, "force", False):
            log.info("already_translated", out=str(out_path))
            raise AlreadyTranslated(out_path)
        backup = _backup_existing(out_path)
        log.info("backed_up_existing", backup=str(backup))

    # Daily cost cap — fail fast.
    cost_tracker.check_daily_cap(settings.max_cost_cents_per_day)

    if media.suffix.lower() in SUB_EXTENSIONS:
        raw_path = media
        log.info("direct_sub_file", path=str(media), source_lang=source_lang)
    else:
        # Step A: try embedded subtitle tracks via ffprobe.
        tracks = await list_sub_tracks(media)

        if tracks:
            track = pick_source_track(tracks, req.source_track_index, target_lang)
            source_lang = req.source_lang or track.language or "auto"
            detected_track_index = track.index
            log.info("picked_track", index=track.index, codec=track.codec, lang=source_lang)

            workdir = settings.media_root / ".translarr" / media.stem
            workdir.mkdir(parents=True, exist_ok=True)
            ext = ".ass" if track.codec in {"ass", "ssa"} else ".srt"
            raw_path = workdir / f"source.{track.index}{ext}"
            await extract_track(media, track.index, raw_path)
        else:
            # Step B: fall back to sidecar subtitle files next to the media.
            # Common patterns: <basename>.<lang>.srt, <basename>.<lang>.hi.srt,
            # <basename>.srt. Pick the first one whose language tag is NOT the
            # target_lang. Source-lang hint in the request wins over filename
            # parsing.
            sidecar = _find_sidecar_subtitle(media, target_lang)
            if sidecar is None:
                raise NoSourceSubtitles(
                    f"no_source_subtitles: {media.name}. Looked for embedded sub tracks "
                    f"(ffprobe found none) and for sidecar files next to the media "
                    f"({media.parent}). v0.8a+ will add provider-fetch fallback; "
                    f"v0.9 adds Whisper-from-audio. For v0.1, add a subtitle track manually."
                )
            raw_path = sidecar.path
            source_lang = req.source_lang or sidecar.lang or "auto"
            log.info(
                "sidecar_sub_file",
                path=str(raw_path),
                detected_lang=sidecar.lang,
                source_lang=source_lang,
            )

    subs = _load_subs_with_encoding_fallback(raw_path)
    log.info("loaded_sub_file", events=len(subs.events))

    provider = get_provider()
    start = time.monotonic()

    lines = [_strip_for_translation(ev.text) for ev in subs.events]
    translations: list[str] = []
    prior: list[str] = []
    batch_size = 30
    tokens_in_total = 0
    tokens_out_total = 0

    async def _run_with_timeout() -> None:
        nonlocal tokens_in_total, tokens_out_total
        for i in range(0, len(lines), batch_size):
            batch = lines[i : i + batch_size]
            out = await provider.translate_batch(
                lines=batch,
                source_lang=source_lang,
                target_lang=target_lang,
                prior_context=prior[-settings.context_window_lines :],
            )
            translations.extend(out)
            prior.extend(out)

            # Cheap token estimate (4 chars/token) — refined when providers expose usage in v0.4+.
            tokens_in_total += sum(len(s) for s in batch) // 4 + 300  # 300 sys prompt
            tokens_out_total += sum(len(s) for s in out) // 4

            # Per-job cost gate at batch boundary.
            est = cost_tracker.estimate_cents(provider.model, tokens_in_total, tokens_out_total)
            cost_tracker.check_job_cap(est, settings.max_cost_cents_per_job)

    try:
        await asyncio.wait_for(_run_with_timeout(), timeout=settings.job_timeout_seconds)
    except TimeoutError as e:
        raise TimeoutError(
            f"job_timeout: exceeded {settings.job_timeout_seconds}s before completion"
        ) from e

    # Reconcile LLM output count to source event count. The pipeline
    # invariant (see /CLAUDE.md "Subtitle pipeline invariants") is that
    # output_count == input_count. LLMs occasionally drop or merge
    # adjacent lines; we pad with the original text (verbatim
    # passthrough is safer than empty) or truncate as needed, then log
    # the deviation so the critic pass (v0.4) can flag it.
    expected = len(subs.events)
    if len(translations) < expected:
        log.warning(
            "translation_undercount",
            expected=expected,
            got=len(translations),
            padded=expected - len(translations),
        )
        for i in range(len(translations), expected):
            translations.append(_strip_for_translation(subs.events[i].text))
    elif len(translations) > expected:
        log.warning(
            "translation_overcount",
            expected=expected,
            got=len(translations),
            truncated=len(translations) - expected,
        )
        del translations[expected:]

    events = [
        SubEvent(start_ms=ev.start, end_ms=ev.end, text=t, style=ev.style)
        for ev, t in zip(subs.events, translations, strict=True)
    ]
    adapted = adapt_events_for_cps(events, max_cps=settings.reading_rate_cps)

    out_subs = pysubs2.SSAFile()
    for ev in adapted:
        new_ev = pysubs2.SSAEvent(start=ev.start_ms, end=ev.end_ms, text=ev.text)
        if ev.style:
            new_ev.style = ev.style
        out_subs.events.append(new_ev)

    out_subs.save(str(out_path), format_="srt")

    elapsed = time.monotonic() - start
    cost_cents = cost_tracker.estimate_cents(provider.model, tokens_in_total, tokens_out_total)
    cost_tracker.record(cost_cents)

    # Library refresh — fire-and-forget so it doesn't block job completion.
    fire_and_forget(library_refresh.refresh_libraries_after(out_path))

    log.info(
        "translated",
        source_events=len(translations),
        output_events=len(adapted),
        seconds=elapsed,
        out=str(out_path),
        cost_cents=cost_cents,
    )

    return TranslateResponse(
        output_path=out_path,
        source_events=len(translations),
        output_events=len(adapted),
        lines_translated=len(translations),  # back-compat
        duration_seconds=elapsed,
        model=provider.model,
        provider=provider.name,
        cost_cents=cost_cents,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        source_lang=source_lang,
        source_track_index=detected_track_index,
    )


def _strip_for_translation(text: str) -> str:
    return text.replace("\\N", " ").replace("\n", " ").strip()


def _load_subs_with_encoding_fallback(path: Path):
    """Try common subtitle encodings in order: UTF-8 (most modern files),
    UTF-8-SIG (UTF-8 with BOM), CP1252 / Windows-1252 (legacy DVD rips),
    Latin-1 (older European releases). Raises ValueError if all fail."""
    last_err: Exception | None = None
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            subs = pysubs2.load(str(path), encoding=encoding)
            if encoding != "utf-8":
                log.info("sub_encoding_fallback", path=str(path), encoding=encoding)
            return subs
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise ValueError(
        f"could not decode {path.name} as utf-8/utf-8-sig/cp1252/latin-1: {last_err}"
    )


# ===== Sidecar subtitle discovery =========================================


@dataclass
class _SidecarSub:
    """A subtitle file living next to its media, e.g. movie.en.srt."""

    path: Path
    lang: str | None  # ISO 639-1 or -2 if detectable from filename, else None


# Language hints that may appear in sidecar filenames. Includes the common
# "language flag" tokens Plex/Emby use (hi for hearing-impaired, sdh, forced).
_LANG_FLAGS = {"hi", "sdh", "cc", "forced"}

# Full English language names (Plex/Emby use these in nested Subtitles/ folders)
# mapped to ISO 639-1. Covers the languages we're likely to translate from.
_LANG_NAME_TO_CODE: dict[str, str] = {
    "english": "en", "spanish": "es", "german": "de", "french": "fr",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
    "polish": "pl", "swedish": "sv", "norwegian": "no", "danish": "da",
    "finnish": "fi", "czech": "cs", "hungarian": "hu", "romanian": "ro",
    "greek": "el", "turkish": "tr", "arabic": "ar", "hebrew": "he",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "vietnamese": "vi",
    "thai": "th", "indonesian": "id", "malay": "ms", "hindi": "hi",
    "bengali": "bn", "tamil": "ta", "telugu": "te", "ukrainian": "uk",
    "bulgarian": "bg", "croatian": "hr", "serbian": "sr", "slovak": "sk",
    "slovenian": "sl", "estonian": "et", "latvian": "lv", "lithuanian": "lt",
    "icelandic": "is", "catalan": "ca",
}


def _find_sidecar_subtitle(media: Path, target_lang: str) -> _SidecarSub | None:
    """Look for a subtitle file next to `media` whose language is NOT target_lang.

    Filename patterns we recognize:
      <basename>.srt                            (language unknown)
      <basename>.<lang>.srt                     (e.g., .en.srt, .ru.srt)
      <basename>.<lang>.<flag>.srt              (e.g., .en.hi.srt, .en.sdh.srt)

    Where <lang> is ISO 639-1 (2 chars) or 639-2 (3 chars). The function
    also accepts .ass / .ssa / .vtt / .sub extensions.

    Returns the best non-target-lang candidate, or None.
    """
    basename = media.stem  # 'Movie' or 'Show.S01E04.WEB-DL'
    target_lower = target_lang.lower()
    target_alt = _to_iso639_2(target_lower)

    # Scan media.parent AND a sibling Subtitles/ subdir (Plex convention).
    scan_dirs: list[Path] = [media.parent]
    subs_dir = media.parent / "Subtitles"
    if subs_dir.is_dir():
        scan_dirs.append(subs_dir)

    candidates: list[_SidecarSub] = []
    for d in scan_dirs:
        for entry in d.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in SUB_EXTENSIONS:
                continue
            # Must share the media's basename (case-sensitive).
            entry_stem = entry.stem
            if not entry_stem.startswith(basename):
                continue
            tail = entry_stem[len(basename):].lstrip(".")
            lang = _parse_sidecar_lang(tail)
            candidates.append(_SidecarSub(path=entry, lang=lang))

    if not candidates:
        return None

    # Prefer candidates whose detected language is not the target language.
    # Then prefer non-flag (plain language) over flagged (hi/sdh) variants.
    def score(c: _SidecarSub) -> tuple:
        lang = (c.lang or "").lower()
        # Exclude target_lang (and its iso639-2 alias)
        is_target = lang in (target_lower, target_alt)
        # Flag-tagged files (HI/SDH/forced) — slight deprioritization
        name_tail = c.path.stem[len(basename):].lstrip(".").lower()
        has_flag = any(name_tail.endswith("." + f) or name_tail == f for f in _LANG_FLAGS)
        # Lower tuple wins (sorted ascending)
        return (1 if is_target else 0, 1 if has_flag else 0, str(c.path))

    candidates.sort(key=score)
    chosen = candidates[0]
    # If only candidate is itself in target_lang, that's already-translated;
    # don't pick it — caller will see None and raise the "no source" error.
    if (chosen.lang or "").lower() in (target_lower, target_alt):
        return None
    return chosen


def _parse_sidecar_lang(tail: str) -> str | None:
    """Extract a language code from a sidecar filename tail.

    Examples:
      ''        -> None        (no lang flag)
      'en'      -> 'en'
      'en.hi'   -> 'en' (hi is a flag, not a language)
      'hi'      -> 'hi' (only token: treat as language even though hi is also a flag)
      'jpn'     -> 'jpn'
      'forced'  -> None        (pure flag, no language)
    """
    if not tail:
        return None
    parts = [p for p in tail.split(".") if p]
    if not parts:
        return None
    first = parts[0].lower()
    # Pure flag (forced/sdh/cc — NOT 'hi', which is both Hindi ISO 639-1 AND
    # the hearing-impaired marker) → no language information.
    if first in {"forced", "sdh", "cc"}:
        return None
    # Full English language name (Plex/Emby Subtitles/ folder convention).
    if first in _LANG_NAME_TO_CODE:
        return _LANG_NAME_TO_CODE[first]
    # ISO 639-1 (2 chars) or 639-2 (3 chars).
    if len(first) in (2, 3):
        return first
    return None


def _to_iso639_2(code: str) -> str:
    """Map common ISO 639-1 codes to 639-2 for cross-matching."""
    return {
        "en": "eng", "es": "spa", "de": "deu", "fr": "fra", "ja": "jpn",
        "ko": "kor", "ru": "rus", "hi": "hin", "zh": "zho", "pt": "por",
        "it": "ita", "nl": "nld", "pl": "pol", "tr": "tur", "ar": "ara",
    }.get(code, code)
