import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import pysubs2
import structlog

from server import cost_tracker
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
        tracks = await list_sub_tracks(media)
        if not tracks:
            raise ValueError(f"no_source_subtitles: {media.name}. v0.8a+ will add provider-fetch fallback; v0.9 adds Whisper-from-audio. For v0.1, add a subtitle track manually.")

        track = pick_source_track(tracks, req.source_track_index, target_lang)
        source_lang = req.source_lang or track.language or "auto"
        log.info("picked_track", index=track.index, codec=track.codec, lang=source_lang)

        workdir = settings.media_root / ".translarr" / media.stem
        workdir.mkdir(parents=True, exist_ok=True)
        ext = ".ass" if track.codec in {"ass", "ssa"} else ".srt"
        raw_path = workdir / f"source.{track.index}{ext}"
        await extract_track(media, track.index, raw_path)

    subs = pysubs2.load(str(raw_path))
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
    )


def _strip_for_translation(text: str) -> str:
    return text.replace("\\N", " ").replace("\n", " ").strip()
