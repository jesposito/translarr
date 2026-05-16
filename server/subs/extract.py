import asyncio
import json
import shutil
from pathlib import Path

# Per-subprocess timeouts. The pipeline already has a top-level
# `JOB_TIMEOUT_SECONDS` (default 30 min) for the whole translation, but
# letting ffprobe/ffmpeg hang for that long ties up a worker on a single
# bad file. These bounds catch wedged decodes early so the worker can
# fail fast, surface a clear error, and move on. Raise the values if
# you regularly translate multi-hour 4K HDR releases with embedded
# Dolby Vision sub tracks — those can be legitimately slow to probe.
FFPROBE_TIMEOUT_SECONDS = 30.0
FFMPEG_EXTRACT_TIMEOUT_SECONDS = 120.0


# Text-based subtitle codecs that ffmpeg can extract to SRT/ASS.
# Bitmap formats (PGS, VOBSUB, DVDSUB) require OCR and are not supported.
TEXT_SUB_CODECS = {
    "subrip", "srt", "ass", "ssa", "vtt", "webvtt",
    "mov_text", "txt", "microdvd", "mpl2",
}

# ISO 639-2 → 639-1 mapping for the most common subtitle languages.
_ISO_639_2_TO_1: dict[str, str] = {
    "eng": "en", "spa": "es", "deu": "de", "ger": "de", "fra": "fr", "fre": "fr",
    "ita": "it", "por": "pt", "nld": "nl", "dut": "nl", "rus": "ru", "pol": "pl",
    "swe": "sv", "nor": "no", "nob": "no", "dan": "da", "fin": "fi", "ces": "cs",
    "cze": "cs", "hun": "hu", "ron": "ro", "rum": "ro", "ell": "el", "gre": "el",
    "tur": "tr", "ara": "ar", "heb": "he", "jpn": "ja", "kor": "ko",
    "chi": "zh", "zho": "zh", "cmn": "zh", "vie": "vi", "tha": "th",
    "ind": "id", "may": "ms", "msa": "ms", "hin": "hi", "ben": "bn",
    "tam": "ta", "tel": "te", "ukr": "uk", "bul": "bg", "hrv": "hr",
    "srp": "sr", "slk": "sk", "slv": "sl", "est": "et", "lav": "lv",
    "lit": "lt", "isl": "is", "ice": "is", "cat": "ca", "tgl": "tl",
}


def _normalize_lang(lang: str | None) -> str | None:
    """Normalize a language code to ISO 639-1 (2-letter) for comparison.

    ffprobe reports ISO 639-2 (3-letter) codes like 'eng', 'chi', 'spa'.
    Our settings use ISO 639-1 like 'en', 'zh', 'es'. Normalizing both
    sides before comparing avoids the mismatch that caused the pipeline
    to pick an English PGS track as "non-target".
    """
    if not lang:
        return None
    low = lang.strip().lower()
    if len(low) == 2:
        return low
    return _ISO_639_2_TO_1.get(low, low)


class TrackInfo:
    def __init__(self, index: int, codec: str, language: str | None, title: str | None) -> None:
        self.index = index
        self.codec = codec
        self.language = language
        self.title = title

    @property
    def is_text(self) -> bool:
        """True if the codec is text-based (extractable to SRT/ASS)."""
        return self.codec.lower() in TEXT_SUB_CODECS

    def __repr__(self) -> str:
        return f"TrackInfo(idx={self.index}, codec={self.codec}, lang={self.language})"


async def list_sub_tracks(media_path: Path) -> list[TrackInfo]:
    """Return all subtitle tracks in the media file."""
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-of", "json",
        str(media_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFPROBE_TIMEOUT_SECONDS
        )
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"ffprobe timed out after {FFPROBE_TIMEOUT_SECONDS:.0f}s on {media_path.name}"
        ) from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')}")

    data = json.loads(stdout)
    out: list[TrackInfo] = []
    for s in data.get("streams", []):
        tags = s.get("tags", {})
        out.append(
            TrackInfo(
                index=s["index"],
                codec=s.get("codec_name", "unknown"),
                language=tags.get("language"),
                title=tags.get("title"),
            )
        )
    return out


async def extract_track(media_path: Path, track_index: int, output_path: Path) -> Path:
    """Extract a single subtitle track to .srt or .ass on disk."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i", str(media_path),
        "-map", f"0:{track_index}",
        "-c:s", "copy",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_EXTRACT_TIMEOUT_SECONDS
        )
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"ffmpeg extract timed out after {FFMPEG_EXTRACT_TIMEOUT_SECONDS:.0f}s "
            f"on {media_path.name}:track{track_index}"
        ) from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {stderr.decode(errors='replace')}")
    return output_path


def pick_source_track(
    tracks: list[TrackInfo],
    requested_index: int | None,
    target_lang: str,
) -> TrackInfo:
    """Pick which sub track to translate from.

    Filters out bitmap subtitle formats (PGS, VOBSUB) that can't be
    extracted to text. Normalizes language codes to ISO 639-1 before
    comparing so ``eng`` matches ``en``, ``chi`` matches ``zh``, etc.

    Priority: explicit index > non-target-lang text track > first text track.
    """
    if not tracks:
        raise ValueError("no subtitle tracks found")

    text_tracks = [t for t in tracks if t.is_text]
    if not text_tracks:
        raise ValueError(
            "no text-based subtitle tracks found "
            f"(only bitmap formats like PGS/VOBSUB in {len(tracks)} tracks)"
        )

    if requested_index is not None:
        for t in text_tracks:
            if t.index == requested_index:
                return t
        raise ValueError(f"track index {requested_index} not in file (or is not a text track)")

    target_norm = _normalize_lang(target_lang)
    for t in text_tracks:
        track_norm = _normalize_lang(t.language)
        if track_norm and track_norm != target_norm:
            return t
    return text_tracks[0]


def has_translateable_track(tracks: list[TrackInfo], target_lang: str) -> bool:
    """True iff the file has at least one text-based subtitle track in a
    language other than the target.

    Used by /translate/sync's "smart" provider mode to short-circuit
    requests for files that have nothing to translate from (e.g. an
    English-only release when target_lang is "en", or a release that
    only has PGS/VOBSUB bitmap subs).

    A text track whose language is unknown (no ``language`` tag in the
    container) counts as translateable — we can't tell the language
    without ffprobe-extracting it, and the worker will detect the actual
    language at extraction time anyway.
    """
    target_norm = _normalize_lang(target_lang)
    for t in tracks:
        if not t.is_text:
            continue
        track_norm = _normalize_lang(t.language)
        if track_norm is None or track_norm != target_norm:
            return True
    return False


def has_only_bitmap_tracks(tracks: list[TrackInfo]) -> bool:
    """True iff the file has subtitle tracks but none are text-based.

    Used by the worker to treat PGS/VOBSUB-only files as a terminal skip
    (no retry) instead of a recoverable ``ValueError`` that burns three
    retry attempts before giving up. Translarr can't translate bitmap
    subs without OCR (planned for v1.0+).
    """
    return bool(tracks) and not any(t.is_text for t in tracks)
