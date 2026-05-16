"""File browser API — list media directories, detect translation coverage.

Let users explore their media library from the Translarr Web UI and see
which files already have translations. This is the foundation for the
coverage view and one-click translate.
"""

from __future__ import annotations

import re
from pathlib import Path

from server.config import settings

# Extensions we consider media files (video containers).
MEDIA_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".wmv", ".flv", ".webm",
    ".m4v", ".mov", ".ts", ".m2ts", ".mpg", ".mpeg",
    ".ogv", ".rm", ".rmvb", ".vob", ".3gp",
}

# Files/dirs to skip when listing.
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".git", ".gitignore", "@eaDir"}
SKIP_PREFIXES = ("._",)  # macOS resource forks


def browse_path(relative_path: str = "") -> dict:
    """List contents of a directory within MEDIA_ROOT.

    Returns a dict with:
    - path: the current directory path (relative to MEDIA_ROOT)
    - parent: the parent directory path (or None if at root)
    - dirs: list of subdirectories
    - files: list of media files with translation status
    """
    root = settings.media_root.resolve()
    target = (root / relative_path).resolve() if relative_path else root

    # Path traversal guard.
    try:
        target.relative_to(root)
    except ValueError:
        return {"error": "path outside MEDIA_ROOT", "path": relative_path}

    if not target.is_dir():
        return {"error": "not a directory", "path": relative_path}

    # Compute parent path. None means we're at MEDIA_ROOT (no parent).
    parent_rel: str | None
    try:
        parent_rel = str(target.parent.relative_to(root))
        if parent_rel == ".":
            parent_rel = None
    except ValueError:
        parent_rel = None

    dirs = []
    files = []

    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return {"error": "permission denied", "path": relative_path}

    for entry in entries:
        name = entry.name
        if name in SKIP_NAMES or any(name.startswith(p) for p in SKIP_PREFIXES):
            continue

        if entry.is_dir():
            dirs.append({
                "name": name,
                "path": str(entry.relative_to(root)),
                "has_media": _dir_has_media(entry),
            })
        elif entry.is_file() and entry.suffix.lower() in MEDIA_EXTENSIONS:
            # Check for existing translations.
            rel = str(entry.relative_to(root))
            translations = _find_translations(entry)
            files.append({
                "name": name,
                "path": rel,
                "size": entry.stat().st_size,
                "translated": len(translations) > 0,
                "translations": translations,
            })

    return {
        "path": relative_path or "/",
        "parent": parent_rel,
        "dirs": dirs,
        "files": files,
        "total_files": len(files),
        "translated_files": sum(1 for f in files if f["translated"]),
    }


def _find_translations(media_path: Path) -> list[dict]:
    """Find .translarr.srt files for a given media file.

    Pattern: <basename>.<lang>.translarr.srt
    """
    stem = media_path.stem
    parent = media_path.parent
    translations = []
    try:
        for f in parent.iterdir():
            if f.name.startswith(stem) and ".translarr." in f.name and f.suffix in (".srt", ".ass", ".ssa", ".vtt"):
                # Extract language from pattern: stem.<lang>.translarr.srt
                match = re.search(r"\.([a-z]{2,3})\.translarr\.", f.name)
                lang = match.group(1) if match else "unknown"
                translations.append({
                    "lang": lang,
                    "path": str(f.relative_to(settings.media_root.resolve())),
                    "size": f.stat().st_size,
                })
    except PermissionError:
        pass
    return translations


def _dir_has_media(directory: Path) -> bool:
    """Quick check if a directory contains media files (1 level deep)."""
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix.lower() in MEDIA_EXTENSIONS:
                return True
            if entry.is_dir() and not entry.name.startswith("."):
                # Recurse one level (for series/season structure).
                for child in entry.iterdir():
                    if child.is_file() and child.suffix.lower() in MEDIA_EXTENSIONS:
                        return True
        return False
    except (PermissionError, OSError):
        return False
