"""Tests for subtitle track listing and selection.

Covers the two bugs fixed 2026-05-15:
1. PGS/bitmap tracks must be filtered out — they can't be extracted to SRT.
2. Language comparison must normalize ISO 639-2 (eng, chi) ↔ 639-1 (en, zh).
"""

from __future__ import annotations

import pytest

from server.subs.extract import TrackInfo, pick_source_track


def _track(index: int, codec: str = "subrip", lang: str | None = None) -> TrackInfo:
    return TrackInfo(index=index, codec=codec, language=lang, title=None)


class TestTrackInfoIsText:
    def test_subrip_is_text(self):
        assert _track(0, codec="subrip").is_text is True

    def test_srt_is_text(self):
        assert _track(0, codec="srt").is_text is True

    def test_ass_is_text(self):
        assert _track(0, codec="ass").is_text is True

    def test_vtt_is_text(self):
        assert _track(0, codec="vtt").is_text is True

    def test_mov_text_is_text(self):
        assert _track(0, codec="mov_text").is_text is True

    def test_pgs_is_not_text(self):
        assert _track(0, codec="pgssub").is_text is False

    def test_hdmv_pgs_is_not_text(self):
        assert _track(0, codec="hdmv_pgs_subtitle").is_text is False

    def test_dvdsub_is_not_text(self):
        assert _track(0, codec="dvd_subtitle").is_text is False

    def test_vobsub_is_not_text(self):
        assert _track(0, codec="dvdsub").is_text is False


class TestPickSourceTrackSkipsBitmap:
    """Regression: pick_source_track must not return PGS/VOBSUB tracks."""

    def test_skips_pgs_picks_srt(self):
        tracks = [
            _track(2, codec="pgssub", lang="eng"),
            _track(3, codec="subrip", lang="chi"),
            _track(4, codec="subrip", lang="chi"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="en")
        assert picked.index == 3
        assert picked.codec == "subrip"

    def test_all_bitmap_raises(self):
        tracks = [
            _track(0, codec="pgssub", lang="eng"),
            _track(1, codec="pgssub", lang="chi"),
        ]
        with pytest.raises(ValueError, match="no text-based subtitle"):
            pick_source_track(tracks, requested_index=None, target_lang="en")

    def test_explicit_pgs_index_rejected(self):
        tracks = [
            _track(0, codec="subrip", lang="chi"),
            _track(1, codec="pgssub", lang="eng"),
        ]
        with pytest.raises(ValueError, match="not a text track"):
            pick_source_track(tracks, requested_index=1, target_lang="en")


class TestPickSourceTrackLanguageNormalization:
    """Regression: ISO 639-2 'eng' must match target_lang 'en'."""

    def test_eng_matches_en(self):
        """English PGS (eng) should NOT be picked when target is 'en'."""
        tracks = [
            _track(2, codec="subrip", lang="eng"),  # English text sub
            _track(3, codec="subrip", lang="chi"),  # Chinese text sub
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="en")
        # Should skip the English track (eng normalizes to en == target).
        assert picked.index == 3
        assert picked.language == "chi"

    def test_chi_normalizes_to_zh(self):
        tracks = [
            _track(0, codec="subrip", lang="eng"),
            _track(1, codec="subrip", lang="chi"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="zh")
        # 'chi' normalizes to 'zh' which IS target, so pick English instead.
        assert picked.index == 0

    def test_spa_normalizes_to_es(self):
        tracks = [
            _track(0, codec="subrip", lang="spa"),
            _track(1, codec="subrip", lang="eng"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="es")
        assert picked.index == 1  # picks eng since spa==es

    def test_fre_normalizes_to_fr(self):
        tracks = [
            _track(0, codec="subrip", lang="fre"),
            _track(1, codec="subrip", lang="eng"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="fr")
        assert picked.index == 1  # picks eng since fre==fr

    def test_two_letter_lang_codes_unchanged(self):
        tracks = [
            _track(0, codec="subrip", lang="en"),
            _track(1, codec="subrip", lang="zh"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="en")
        assert picked.index == 1
        assert picked.language == "zh"

    def test_no_lang_tag_picks_first_text(self):
        tracks = [
            _track(0, codec="subrip", lang=None),
            _track(1, codec="subrip", lang="eng"),
        ]
        picked = pick_source_track(tracks, requested_index=None, target_lang="en")
        # No language → can't confirm it's target → returns first text track.
        assert picked.index == 0
