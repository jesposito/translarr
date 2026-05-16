"""Pipeline-level test that bitmap-only sub files trigger a terminal skip.

Before this change, ``pick_source_track`` raised a plain ``ValueError``
("no text-based subtitle tracks found") for PGS/VOBSUB-only files.
The worker treats ValueError as a recoverable failure -> retries 3
times before giving up, which wastes worker cycles on a known-unsolvable
input (Translarr has no OCR yet).

The pipeline now checks for bitmap-only files BEFORE picking a track and
raises ``NoSourceSubtitles`` instead, which the worker recognises as a
terminal skip ($0, done, no retry).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from server.models import TranslateRequest
from server.subs.extract import TrackInfo
from server.subs.pipeline import NoSourceSubtitles, translate_media


@pytest.fixture
def _fake_media(tmp_path, monkeypatch):
    from server.config import settings

    monkeypatch.setattr(settings, "media_root", tmp_path)
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"fake-mkv")
    return media


@pytest.mark.asyncio
async def test_bitmap_only_raises_no_source_subtitles(_fake_media):
    """PGS/VOBSUB-only file should raise NoSourceSubtitles, not ValueError."""

    async def _fake_tracks(p):
        return [
            TrackInfo(index=0, codec="hdmv_pgs_subtitle", language="rus", title=None),
            TrackInfo(index=1, codec="dvd_subtitle", language="ger", title=None),
        ]

    with (
        patch("server.subs.pipeline.list_sub_tracks", _fake_tracks),
        pytest.raises(NoSourceSubtitles, match="bitmap_subs_only"),
    ):
        await translate_media(
            TranslateRequest(media_path=Path("movie.mkv"), target_lang="en")
        )


@pytest.mark.asyncio
async def test_mixed_bitmap_and_text_still_proceeds(_fake_media):
    """When at least one text track exists, the pipeline picks it normally
    even if there are PGS tracks alongside. NOT a bitmap-only scenario."""
    # The pipeline would normally call extract_track → pysubs2 → LLM. We
    # only want to verify the bitmap-only guard does NOT fire here; we
    # let the LLM stage fail (no API key) as long as it gets past the
    # subtitle-track selection.

    async def _fake_tracks(p):
        return [
            TrackInfo(index=0, codec="hdmv_pgs_subtitle", language="rus", title=None),
            TrackInfo(index=1, codec="subrip", language="jpn", title=None),
        ]

    with (
        patch("server.subs.pipeline.list_sub_tracks", _fake_tracks),
        pytest.raises(Exception) as exc_info,
    ):
        # NoSourceSubtitles would mean the guard wrongly fired.
        await translate_media(
            TranslateRequest(media_path=Path("movie.mkv"), target_lang="en")
        )
    assert not isinstance(exc_info.value, NoSourceSubtitles), (
        f"bitmap-only guard wrongly fired on mixed tracks: {exc_info.value}"
    )
