"""End-to-end translate_media on a direct subtitle file with a fake provider.

A subtitle file passed as media_path skips ffprobe/ffmpeg, so these run
without external tools or an LLM key.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest

from server.models import TranslateRequest
from server.subs import pipeline


class _FakeProvider:
    name = "anthropic"
    model = "claude-sonnet-4-6"

    def __init__(self, fail_on_batch: int | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_on_batch = fail_on_batch

    async def translate_batch(self, lines, source_lang, target_lang, prior_context=None, glossary=None):
        self.calls.append({"lines": lines, "glossary": glossary})
        if self.fail_on_batch is not None and len(self.calls) > self.fail_on_batch:
            raise RuntimeError("provider down")
        # Drop the translation of the second line to simulate a model miss.
        return [f"EN:{ln}" if i != 1 else "" for i, ln in enumerate(lines)]


@pytest.fixture
def recorded(monkeypatch):
    spent: list[int] = []
    monkeypatch.setattr(pipeline.cost_tracker, "record", spent.append)
    monkeypatch.setattr(pipeline.cost_tracker, "check_daily_cap", lambda cap: None)
    monkeypatch.setattr(pipeline, "fire_and_forget", lambda coro: coro.close())
    return spent


def _write_ass(path: Path, n_dialogue: int) -> None:
    subs = pysubs2.SSAFile()
    comment = pysubs2.SSAEvent(start=0, end=1000, text="karaoke template")
    comment.is_comment = True
    subs.events.append(comment)
    for i in range(n_dialogue):
        subs.events.append(pysubs2.SSAEvent(start=i * 3000, end=i * 3000 + 2500, text=f"line {i}"))
    subs.save(str(path))


async def test_skips_comments_and_backfills_dropped_lines(tmp_path, monkeypatch, recorded):
    src = tmp_path / "Show.ru.ass"
    _write_ass(src, 3)
    provider = _FakeProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda: provider)

    res = await pipeline.translate_media(
        TranslateRequest(media_path=src, target_lang="en", source_lang="ru")
    )

    assert provider.calls[0]["lines"] == ["line 0", "line 1", "line 2"]
    texts = [ev.text for ev in pysubs2.load(str(res.output_path))]
    assert texts == ["EN:line 0", "line 1", "EN:line 2"]
    assert len(recorded) == 1


async def test_records_spend_when_job_fails_midway(tmp_path, monkeypatch, recorded):
    src = tmp_path / "Movie.ru.ass"
    _write_ass(src, 45)  # two batches of 30
    monkeypatch.setattr(pipeline, "get_provider", lambda: _FakeProvider(fail_on_batch=1))

    with pytest.raises(RuntimeError, match="provider down"):
        await pipeline.translate_media(
            TranslateRequest(media_path=src, target_lang="en", source_lang="ru")
        )

    assert recorded and recorded[0] > 0


async def test_glossary_reaches_provider(tmp_path, monkeypatch, recorded):
    src = tmp_path / "Show.ja.ass"
    _write_ass(src, 1)
    provider = _FakeProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda: provider)
    monkeypatch.setattr(
        pipeline.glossary, "get_glossary_map", lambda gid, lang: {"tanjiro": "Tanjiro"}
    )

    await pipeline.translate_media(
        TranslateRequest(media_path=src, target_lang="en", source_lang="ja", glossary_id="ds")
    )

    assert provider.calls[0]["glossary"] == {"tanjiro": "Tanjiro"}
