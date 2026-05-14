import asyncio
import json
import shutil
from pathlib import Path


class TrackInfo:
    def __init__(self, index: int, codec: str, language: str | None, title: str | None) -> None:
        self.index = index
        self.codec = codec
        self.language = language
        self.title = title

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
    stdout, stderr = await proc.communicate()
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
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {stderr.decode(errors='replace')}")
    return output_path


def pick_source_track(
    tracks: list[TrackInfo],
    requested_index: int | None,
    target_lang: str,
) -> TrackInfo:
    """Pick which sub track to translate from.

    Priority: explicit index > non-target-lang track > first track.
    """
    if not tracks:
        raise ValueError("no subtitle tracks found")
    if requested_index is not None:
        for t in tracks:
            if t.index == requested_index:
                return t
        raise ValueError(f"track index {requested_index} not in file")
    for t in tracks:
        if t.language and t.language != target_lang:
            return t
    return tracks[0]
