from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from server.config import Settings, settings
from server.subs import extract as extract_module
from server.subs.extract import extract_track, list_sub_tracks
from server.subs.pipeline import _extraction_workdir


class _Process:
    def __init__(
        self,
        *,
        stdout: bytes = b'{"streams": []}',
        stderr: bytes = b"",
        returncode: int = 0,
        output_path: Path | None = None,
        timeout: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.output_path = output_path
        self.timeout = timeout
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.output_path is not None:
            self.output_path.write_text("complete subtitle")
        if self.timeout:
            raise TimeoutError
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_ffprobe_reads_timeout_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, float] = {}
    process = _Process()

    async def wait_for(awaitable: object, *, timeout: float):
        seen["timeout"] = timeout
        return await awaitable  # type: ignore[misc]

    monkeypatch.setattr(extract_module.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(extract_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(extract_module.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(settings, "ffprobe_timeout_seconds", 77.0)

    assert await list_sub_tracks(tmp_path / "movie.mkv") == []
    assert seen["timeout"] == 77.0


@pytest.mark.asyncio
async def test_existing_complete_extraction_skips_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "source.3.srt"
    output.write_text("already complete")
    create = AsyncMock()
    monkeypatch.setattr(extract_module.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(extract_module.shutil, "which", lambda _name: None)

    assert await extract_track(tmp_path / "movie.mkv", 3, output) == output
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_extraction_publishes_only_completed_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "source.3.srt"

    async def create(*args: object, **_kwargs: object) -> _Process:
        return _Process(output_path=Path(str(args[-1])))

    monkeypatch.setattr(extract_module.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(extract_module.asyncio, "create_subprocess_exec", create)

    assert await extract_track(tmp_path / "movie.mkv", 3, output) == output
    assert output.read_text() == "complete subtitle"
    assert not (tmp_path / ".source.3.part.srt").exists()


@pytest.mark.asyncio
async def test_timeout_removes_partial_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "source.3.srt"
    process: _Process | None = None

    async def create(*args: object, **_kwargs: object) -> _Process:
        nonlocal process
        process = _Process(output_path=Path(str(args[-1])), timeout=True)
        return process

    monkeypatch.setattr(extract_module.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(extract_module.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(settings, "ffmpeg_extract_timeout_seconds", 9.0)

    with pytest.raises(TimeoutError, match="timed out after 9s"):
        await extract_track(tmp_path / "movie.mkv", 3, output)

    assert process is not None and process.killed
    assert not output.exists()
    assert not (tmp_path / ".source.3.part.srt").exists()


def test_extraction_cache_key_is_media_specific(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "media_root", tmp_path)
    first = tmp_path / "Movies" / "Example" / "video.mkv"
    second = tmp_path / "TV" / "Example" / "video.mkv"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("movie")
    second.write_text("episode")

    first_key = _extraction_workdir(first)
    assert first_key == _extraction_workdir(first)
    assert first_key != _extraction_workdir(second)

    first.write_text("replacement release")
    assert first_key != _extraction_workdir(first)


@pytest.mark.parametrize(
    "field",
    ["ffprobe_timeout_seconds", "ffmpeg_extract_timeout_seconds"],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_settings_reject_invalid_subprocess_timeouts(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})
