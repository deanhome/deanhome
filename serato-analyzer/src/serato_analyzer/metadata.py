"""Read track metadata and embedded artwork with mutagen.

Shared by every platform.  Writing artwork lives in :mod:`artwork.embed`.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from .fsutil import sha256_bytes

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".aac", ".alac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".opus",
    ".wma",
})


class MetadataError(Exception):
    pass


@dataclass
class ArtworkInfo:
    present: bool = False
    data: bytes | None = field(default=None, repr=False)
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    hash: str | None = None
    readable: bool = True

    @classmethod
    def from_bytes(cls, data: bytes, mime: str | None = None) -> "ArtworkInfo":
        width = height = None
        readable = True
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
                mime = mime or Image.MIME.get(im.format or "", None)
        except Exception:
            readable = False
        return cls(True, data, mime, width, height, sha256_bytes(data), readable)


@dataclass
class TrackMetadata:
    path: Path
    format: str = ""
    artist: str = ""
    title: str = ""
    album: str = ""
    album_artist: str = ""
    year: str = ""
    label: str = ""
    catalog_number: str = ""
    isrc: str = ""
    mb_release_id: str = ""
    mb_recording_id: str = ""
    duration: float | None = None
    artwork: ArtworkInfo = field(default_factory=ArtworkInfo)

    def as_db_values(self) -> dict:
        return {
            "format": self.format, "artist": self.artist, "title": self.title,
            "album": self.album, "album_artist": self.album_artist, "year": self.year,
            "label": self.label, "catalog_number": self.catalog_number, "isrc": self.isrc,
            "mb_release_id": self.mb_release_id, "mb_recording_id": self.mb_recording_id,
            "duration": self.duration,
        }


def _first(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if hasattr(value, "text"):  # ID3 frame
        text = value.text
        value = text[0] if text else ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip()


def open_audio(path: Path):
    try:
        audio = mutagen.File(str(path))
    except mutagen.MutagenError as exc:
        raise MetadataError(f"Could not read {Path(path).name}: {exc}") from exc
    if audio is None:
        raise MetadataError(f"Unsupported or unrecognised audio file: {Path(path).name}")
    return audio


def format_name(audio) -> str:
    if isinstance(audio, MP3):
        return "mp3"
    if isinstance(audio, MP4):
        return "mp4"
    if isinstance(audio, FLAC):
        return "flac"
    if isinstance(audio, AIFF):
        return "aiff"
    if isinstance(audio, WAVE):
        return "wav"
    if isinstance(audio, OggOpus):
        return "opus"
    if isinstance(audio, OggVorbis):
        return "ogg"
    return type(audio).__name__.lower()


# ---------------------------------------------------------------------------
# Tag readers
# ---------------------------------------------------------------------------

def _read_id3(tags: ID3 | None, md: TrackMetadata) -> None:
    if tags is None:
        return
    md.artist = _first(tags.get("TPE1"))
    md.title = _first(tags.get("TIT2"))
    md.album = _first(tags.get("TALB"))
    md.album_artist = _first(tags.get("TPE2"))
    md.year = _first(tags.get("TDRC")) or _first(tags.get("TYER"))
    md.label = _first(tags.get("TPUB"))
    md.isrc = _first(tags.get("TSRC"))
    for frame in tags.getall("TXXX"):
        desc = (frame.desc or "").strip().lower()
        if desc in ("catalognumber", "catalog number", "catalog #"):
            md.catalog_number = _first(frame)
        elif desc == "musicbrainz album id":
            md.mb_release_id = _first(frame)
        elif desc == "label" and not md.label:
            md.label = _first(frame)
    for frame in tags.getall("UFID"):
        if frame.owner == "http://musicbrainz.org" and frame.data:
            md.mb_recording_id = frame.data.decode("ascii", "replace")
    pictures = tags.getall("APIC")
    if pictures:
        front = next((p for p in pictures if p.type == 3), pictures[0])
        md.artwork = ArtworkInfo.from_bytes(front.data, front.mime or None)


def _read_vorbis(tags, md: TrackMetadata) -> None:
    if tags is None:
        return
    get = lambda *keys: next((_first(tags.get(k)) for k in keys if tags.get(k)), "")  # noqa: E731
    md.artist = get("artist")
    md.title = get("title")
    md.album = get("album")
    md.album_artist = get("albumartist", "album artist")
    md.year = get("date", "year", "originaldate")
    md.label = get("label", "organization", "publisher")
    md.catalog_number = get("catalognumber")
    md.isrc = get("isrc")
    md.mb_release_id = get("musicbrainz_albumid")
    md.mb_recording_id = get("musicbrainz_trackid")


def _vorbis_comment_pictures(tags) -> list[Picture]:
    pics = []
    for b64 in (tags.get("metadata_block_picture") or []) if tags else []:
        try:
            pics.append(Picture(base64.b64decode(b64)))
        except Exception:
            continue
    return pics


def _read_mp4(tags, md: TrackMetadata) -> None:
    if tags is None:
        return

    def freeform(name: str) -> str:
        val = tags.get(f"----:com.apple.iTunes:{name}")
        return _first(val)

    md.artist = _first(tags.get("\xa9ART"))
    md.title = _first(tags.get("\xa9nam"))
    md.album = _first(tags.get("\xa9alb"))
    md.album_artist = _first(tags.get("aART"))
    md.year = _first(tags.get("\xa9day"))
    md.label = freeform("LABEL") or freeform("label")
    md.catalog_number = freeform("CATALOGNUMBER")
    md.isrc = freeform("ISRC")
    md.mb_release_id = freeform("MusicBrainz Album Id")
    md.mb_recording_id = freeform("MusicBrainz Track Id")
    covers = tags.get("covr") or []
    if covers:
        cover = covers[0]
        mime = "image/png" if getattr(cover, "imageformat", None) == 14 else "image/jpeg"
        md.artwork = ArtworkInfo.from_bytes(bytes(cover), mime)


def read_metadata(path: Path) -> TrackMetadata:
    path = Path(path)
    audio = open_audio(path)
    md = TrackMetadata(path=path, format=format_name(audio))
    info = getattr(audio, "info", None)
    md.duration = float(getattr(info, "length", 0) or 0) or None

    if isinstance(audio, (MP3, AIFF, WAVE)):
        _read_id3(audio.tags, md)
    elif isinstance(audio, MP4):
        _read_mp4(audio.tags, md)
    elif isinstance(audio, FLAC):
        _read_vorbis(audio.tags, md)
        pics = audio.pictures or _vorbis_comment_pictures(audio.tags)
        if pics:
            front = next((p for p in pics if p.type == 3), pics[0])
            md.artwork = ArtworkInfo.from_bytes(front.data, front.mime or None)
    elif isinstance(audio, (OggVorbis, OggOpus)):
        _read_vorbis(audio.tags, md)
        pics = _vorbis_comment_pictures(audio.tags)
        if pics:
            front = next((p for p in pics if p.type == 3), pics[0])
            md.artwork = ArtworkInfo.from_bytes(front.data, front.mime or None)
    else:
        tags = getattr(audio, "tags", None)
        if tags is not None and hasattr(tags, "get"):
            _read_vorbis(tags, md)
    return md


def read_embedded_artwork(path: Path) -> ArtworkInfo:
    return read_metadata(path).artwork
