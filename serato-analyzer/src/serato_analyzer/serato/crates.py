"""Read Serato crate files (``_Serato_/Subcrates/*.crate``).

A crate is a flat sequence of ``[tag:4][length:4 big-endian][payload]``
records.  ``vrsn`` holds the version string; each ``otrk`` record contains a
``ptrk`` record with the track path as UTF-16 big-endian text, relative to the
volume root of the library.  Sub-crates use ``%%`` in the file name as the
hierarchy separator.  This module only reads crates; it never writes them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from ..platforms import PlatformServices, SeratoLibrary


class CrateError(ValueError):
    pass


@dataclass
class Crate:
    name: str               # display name, "Parent / Child"
    path: Path
    track_paths: list[str] = field(default_factory=list)  # as stored in the crate


def _records(data: bytes):
    off = 0
    while off < len(data):
        if off + 8 > len(data):
            raise CrateError("Truncated crate record header")
        tag = data[off:off + 4]
        (length,) = struct.unpack(">I", data[off + 4:off + 8])
        payload = data[off + 8:off + 8 + length]
        if len(payload) != length:
            raise CrateError("Truncated crate record")
        yield tag, payload
        off += 8 + length


def parse_crate(data: bytes) -> list[str]:
    tracks: list[str] = []
    for tag, payload in _records(data):
        if tag == b"otrk":
            for inner_tag, inner in _records(payload):
                if inner_tag == b"ptrk":
                    tracks.append(inner.decode("utf-16-be"))
    return tracks


def build_crate(track_paths: list[str], version: str = "1.0/Serato ScratchLive Crate") -> bytes:
    """Encode a crate (used by tests and fixtures; never written to a user library)."""

    def rec(tag: bytes, payload: bytes) -> bytes:
        return tag + struct.pack(">I", len(payload)) + payload

    out = rec(b"vrsn", version.encode("utf-16-be"))
    for p in track_paths:
        out += rec(b"otrk", rec(b"ptrk", p.encode("utf-16-be")))
    return out


def crate_display_name(path: PurePath) -> str:
    return " / ".join(part for part in path.stem.split("%%") if part)


def list_crates(library: SeratoLibrary) -> list[Crate]:
    folder = Path(library.subcrates_dir)
    if not folder.is_dir():
        return []
    crates = []
    for f in sorted(folder.glob("*.crate"), key=lambda p: p.name.lower()):
        crates.append(Crate(crate_display_name(f), f))
    return crates


def load_crate(crate: Crate) -> Crate:
    crate.track_paths = parse_crate(crate.path.read_bytes())
    return crate


def crate_track_files(platform: PlatformServices, library: SeratoLibrary, crate: Crate) -> list[Path]:
    """Absolute paths of the crate's tracks (missing files included)."""
    if not crate.track_paths:
        load_crate(crate)
    out = []
    for stored in crate.track_paths:
        try:
            out.append(Path(platform.resolve_crate_track_path(library, stored)))
        except ValueError:
            continue
    return out
