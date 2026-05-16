"""Tests for the small ffprobe-result helpers used by the smart provider
mode and the worker's terminal-skip logic."""

from __future__ import annotations

from server.subs.extract import (
    TrackInfo,
    has_only_bitmap_tracks,
    has_translateable_track,
)


def _t(idx, codec, lang=None):
    return TrackInfo(index=idx, codec=codec, language=lang, title=None)


class TestHasTranslateableTrack:
    def test_foreign_text_track_is_translateable(self):
        tracks = [_t(0, "subrip", "rus")]
        assert has_translateable_track(tracks, "en") is True

    def test_target_lang_text_only_is_not(self):
        tracks = [_t(0, "subrip", "eng")]
        assert has_translateable_track(tracks, "en") is False

    def test_iso_639_2_normalises_to_target(self):
        # "eng" (3-letter) must compare equal to "en" (2-letter).
        tracks = [_t(0, "subrip", "eng")]
        assert has_translateable_track(tracks, "en") is False

    def test_unknown_lang_text_track_is_translateable(self):
        # No language tag — we can't tell, so let the worker handle it.
        tracks = [_t(0, "subrip", None)]
        assert has_translateable_track(tracks, "en") is True

    def test_only_bitmap_tracks_not_translateable(self):
        # PGS in a foreign lang still can't be translated without OCR.
        tracks = [_t(0, "hdmv_pgs_subtitle", "rus")]
        assert has_translateable_track(tracks, "en") is False

    def test_mixed_keeps_foreign_text(self):
        tracks = [
            _t(0, "hdmv_pgs_subtitle", "rus"),
            _t(1, "subrip", "jpn"),
        ]
        assert has_translateable_track(tracks, "en") is True

    def test_empty_tracks_not_translateable(self):
        assert has_translateable_track([], "en") is False

    def test_target_normalisation_for_non_english(self):
        tracks = [_t(0, "subrip", "deu")]
        assert has_translateable_track(tracks, "de") is False
        assert has_translateable_track(tracks, "en") is True


class TestHasOnlyBitmapTracks:
    def test_pgs_only_is_bitmap_only(self):
        assert has_only_bitmap_tracks([_t(0, "hdmv_pgs_subtitle", "rus")]) is True

    def test_vobsub_only_is_bitmap_only(self):
        assert has_only_bitmap_tracks([_t(0, "dvd_subtitle", "rus")]) is True

    def test_empty_is_not_bitmap_only(self):
        # No tracks at all is a different condition (handled by the
        # sidecar fallback path); has_only_bitmap_tracks must be False.
        assert has_only_bitmap_tracks([]) is False

    def test_text_only_is_not_bitmap_only(self):
        assert has_only_bitmap_tracks([_t(0, "subrip", "rus")]) is False

    def test_mixed_text_and_bitmap_is_not_bitmap_only(self):
        tracks = [
            _t(0, "hdmv_pgs_subtitle", "rus"),
            _t(1, "subrip", "jpn"),
        ]
        assert has_only_bitmap_tracks(tracks) is False
