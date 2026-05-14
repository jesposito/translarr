from pathlib import Path

import pytest

from server.subs.pipeline import _backup_existing, _output_path


def test_output_filename_uses_translarr_infix(tmp_path: Path):
    media = tmp_path / "Movie.2025.WEB-DL.mkv"
    media.touch()
    out = _output_path(media, "en")
    assert out.name == "Movie.2025.WEB-DL.en.translarr.srt"
    assert out.parent == tmp_path


def test_output_filename_for_ass_input(tmp_path: Path):
    media = tmp_path / "demon-ru.ass"
    media.touch()
    out = _output_path(media, "en")
    # The pattern strips one suffix via stem; .ass → demon-ru
    assert out.name == "demon-ru.en.translarr.srt"


def test_backup_renames_with_timestamp(tmp_path: Path):
    p = tmp_path / "Movie.en.translarr.srt"
    p.write_text("old")
    backup = _backup_existing(p)
    assert backup.exists()
    assert not p.exists()
    assert ".bak.srt" in backup.name
    assert backup.read_text() == "old"
