"""Folder scanner: find audio files and record their metadata and artwork state."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .artwork.service import ArtworkService
from .db import Database
from .metadata import AUDIO_EXTENSIONS, MetadataError, read_metadata
from .platforms import OUTPUT_DIR_NAME, SERATO_DIR_NAME, PlatformServices

SKIP_DIRS = {SERATO_DIR_NAME, OUTPUT_DIR_NAME, "$RECYCLE.BIN", "System Volume Information",
             ".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems"}


@dataclass
class ScanErrors:
    permission_denied: list[tuple[Path, str]] = field(default_factory=list)
    unreadable: list[tuple[Path, str]] = field(default_factory=list)


def iter_audio_files(root: Path, recursive: bool = True, errors: ScanErrors | None = None,
                     hint: Callable[[Path], str] | None = None) -> Iterator[Path]:
    root = Path(root)

    def onerror(exc: OSError) -> None:
        if errors is not None and isinstance(exc, PermissionError):
            p = Path(exc.filename or root)
            errors.permission_denied.append((p, hint(p) if hint else str(exc)))

    if not recursive:
        try:
            entries = sorted(root.iterdir())
        except PermissionError as exc:
            onerror(exc)
            return
        for p in entries:
            if p.is_file() and _is_audio(p):
                yield p
        return
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if _is_audio(p):
                yield p


def _is_audio(p: Path) -> bool:
    # Skip macOS AppleDouble files ("._track.mp3") found on exFAT/FAT drives.
    return p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith("._")


class LibraryScanner:
    def __init__(self, db: Database, artwork: ArtworkService, platform: PlatformServices) -> None:
        self.db = db
        self.artwork = artwork
        self.platform = platform

    def scan_file(self, path: Path, scan_root: Path | None = None, force: bool = False) -> int:
        path = Path(path)
        st = path.stat()
        existing = self.db.get_track_by_path(str(path))
        if (existing and not force and existing["file_size"] == st.st_size
                and existing["mtime"] == st.st_mtime and not existing["read_error"]):
            if scan_root and not existing["scan_root"]:
                self.db.upsert_track(str(path), scan_root=str(scan_root))
            return int(existing["id"])
        try:
            md = read_metadata(path)
        except MetadataError as exc:
            return self.db.upsert_track(str(path), scan_root=str(scan_root) if scan_root else None,
                                        file_size=st.st_size, mtime=st.st_mtime, read_error=str(exc),
                                        format=path.suffix.lower().lstrip("."))
        track_id = self.db.upsert_track(
            str(path), scan_root=str(scan_root) if scan_root else None, file_size=st.st_size,
            mtime=st.st_mtime, read_error=None, **md.as_db_values())
        self.artwork.record_scan(track_id, md)
        return track_id
