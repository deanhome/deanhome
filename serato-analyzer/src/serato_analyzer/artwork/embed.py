"""Embed artwork into audio files without touching the audio.

Only tag structures are rewritten: no audio is decoded or re-encoded.  Every
write goes to a temporary copy first, which is verified (picture present,
audio payload byte-identical, other tags unchanged) before it replaces the
destination.  An existing destination is backed up first.
"""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.ogg import OggPage
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from ..fsutil import replace_with_retry, sha256_bytes, sha256_file, temp_path_beside
from ..metadata import MetadataError, open_audio, read_embedded_artwork
from .backups import BackupManager

EMBEDDABLE_EXTENSIONS = frozenset({".mp3", ".m4a", ".mp4", ".alac", ".flac", ".aif", ".aiff",
                                   ".wav", ".ogg", ".opus"})
# Picture types treated as "the cover": front cover and "other".
_COVER_TYPES = (0, 3)


class EmbedError(Exception):
    pass


class UnsupportedFormatError(EmbedError):
    pass


class ExistingArtworkError(EmbedError):
    """The file already has artwork and replacement was not explicitly allowed."""


class VerificationError(EmbedError):
    pass


def supports_embedding(path: Path) -> bool:
    return Path(path).suffix.lower() in EMBEDDABLE_EXTENSIONS


# ---------------------------------------------------------------------------
# Audio payload fingerprints
# ---------------------------------------------------------------------------

def _id3v2_end(data: bytes) -> int:
    off = 0
    while data[off:off + 3] == b"ID3" and len(data) >= off + 10:
        size = 0
        for b in data[off + 6:off + 10]:
            size = (size << 7) | (b & 0x7F)
        footer = 10 if data[off + 5] & 0x10 else 0
        off += 10 + size + footer
    return off


def _mp3_payload(data: bytes) -> bytes:
    start = _id3v2_end(data)
    end = len(data)
    if end - start >= 128 and data[end - 128:end - 125] == b"TAG":
        end -= 128
    return data[start:end]


def _iff_payload(data: bytes, big_endian: bool, keep: tuple[bytes, ...]) -> bytes:
    """Concatenate the audio-bearing chunks of a RIFF (WAV) or FORM (AIFF) file."""
    fmt = ">I" if big_endian else "<I"
    out = io.BytesIO()
    off = 12
    while off + 8 <= len(data):
        cid = data[off:off + 4]
        (size,) = struct.unpack(fmt, data[off + 4:off + 8])
        body = data[off + 8:off + 8 + size]
        if cid in keep:
            out.write(cid + body)
        off += 8 + size + (size & 1)
    return out.getvalue()


def _flac_payload(data: bytes) -> bytes:
    off = _id3v2_end(data)
    if data[off:off + 4] != b"fLaC":
        raise EmbedError("Not a FLAC stream")
    off += 4
    streaminfo = b""
    while off + 4 <= len(data):
        header = data[off]
        length = int.from_bytes(data[off + 1:off + 4], "big")
        if header & 0x7F == 0:
            streaminfo = data[off + 4:off + 4 + length]
        off += 4 + length
        if header & 0x80:
            break
    return streaminfo + data[off:]


def _mp4_payload(data: bytes) -> bytes:
    out = io.BytesIO()
    off = 0
    while off + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[off:off + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            header = 16
        elif size == 0:
            size = len(data) - off
        if size < header:
            break
        if kind == b"mdat":
            out.write(data[off + header:off + size])
        off += size
    return out.getvalue()


def _ogg_payload(path: Path, header_packets: int) -> bytes:
    pages = []
    with open(path, "rb") as fh:
        while True:
            try:
                pages.append(OggPage(fh))
            except EOFError:
                break
    if not pages:
        raise EmbedError("No Ogg pages found")
    serial = pages[0].serial
    packets = OggPage.to_packets([p for p in pages if p.serial == serial], strict=False)
    h = hashlib.sha256()
    for pkt in packets[header_packets:]:
        h.update(len(pkt).to_bytes(4, "big"))
        h.update(pkt)
    return h.digest()


def audio_fingerprint(path: Path) -> str:
    """SHA-256 of the audio payload only (ignores tag blocks)."""
    path = Path(path)
    audio = open_audio(path)
    if isinstance(audio, OggOpus):
        return sha256_bytes(_ogg_payload(path, 2))
    if isinstance(audio, OggVorbis):
        return sha256_bytes(_ogg_payload(path, 3))
    data = path.read_bytes()
    if isinstance(audio, MP3):
        payload = _mp3_payload(data)
    elif isinstance(audio, WAVE):
        payload = _iff_payload(data, False, (b"fmt ", b"data"))
    elif isinstance(audio, AIFF):
        payload = _iff_payload(data, True, (b"COMM", b"SSND"))
    elif isinstance(audio, FLAC):
        payload = _flac_payload(data)
    elif isinstance(audio, MP4):
        payload = _mp4_payload(data)
    else:
        raise UnsupportedFormatError(f"Cannot fingerprint {type(audio).__name__} files")
    return sha256_bytes(payload)


# ---------------------------------------------------------------------------
# Tag snapshots (everything except pictures)
# ---------------------------------------------------------------------------

def tag_snapshot(path: Path) -> dict[str, str]:
    audio = open_audio(Path(path))
    tags = audio.tags
    snap: dict[str, str] = {}
    if tags is None:
        return snap
    if isinstance(tags, ID3):
        for key, frame in tags.items():
            if key.startswith("APIC"):
                continue
            snap[key] = repr(frame)
    elif isinstance(audio, MP4):
        for key, value in tags.items():
            if key == "covr":
                continue
            snap[key] = repr(value)
    else:
        for key, value in tags.items():
            if key.lower() == "metadata_block_picture":
                continue
            snap[key.lower()] = repr(value)
    return snap


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _image_dims(data: bytes) -> tuple[int, int, str]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        mime = "image/png" if im.format == "PNG" else "image/jpeg"
        return im.size[0], im.size[1], mime


def _flac_picture(data: bytes) -> Picture:
    w, h, mime = _image_dims(data)
    pic = Picture()
    pic.type = 3
    pic.mime = mime
    pic.desc = "Cover"
    pic.width, pic.height, pic.depth = w, h, 24
    pic.data = data
    return pic


def embed_artwork(path: Path, image: bytes, replace_existing: bool = False) -> None:
    """Embed *image* (JPEG/PNG bytes) as the front cover of *path*, in place.

    Call this on a temporary copy; use :func:`safe_embed` for the full
    copy/verify/backup/replace workflow.
    """
    path = Path(path)
    if not supports_embedding(path):
        raise UnsupportedFormatError(f"{path.suffix or 'This'} files do not support embedded artwork here.")
    audio = open_audio(path)
    _w, _h, mime = _image_dims(image)

    if isinstance(audio, (MP3, AIFF, WAVE)):
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        version = tags.version[1] if tags.version and tags.version[1] in (3, 4) else 3
        covers = [f for f in tags.getall("APIC") if f.type in _COVER_TYPES]
        if covers and not replace_existing:
            raise ExistingArtworkError("The file already has embedded artwork.")
        for frame in covers:
            del tags[frame.HashKey]
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image))
        audio.save(v2_version=version)
    elif isinstance(audio, FLAC):
        covers = [p for p in audio.pictures if p.type in _COVER_TYPES]
        if covers and not replace_existing:
            raise ExistingArtworkError("The file already has embedded artwork.")
        audio.metadata_blocks = [b for b in audio.metadata_blocks
                                 if not (isinstance(b, Picture) and b.type in _COVER_TYPES)]
        audio.add_picture(_flac_picture(image))
        audio.save()
    elif isinstance(audio, MP4):
        if audio.tags is None:
            audio.add_tags()
        if audio.tags.get("covr") and not replace_existing:
            raise ExistingArtworkError("The file already has embedded artwork.")
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(image, imageformat=fmt)]
        audio.save()
    elif isinstance(audio, (OggVorbis, OggOpus)):
        if audio.tags is None:
            audio.add_tags()
        existing = audio.tags.get("metadata_block_picture") or []
        keep = []
        for b64 in existing:
            try:
                pic = Picture(base64.b64decode(b64))
            except Exception:
                keep.append(b64)
                continue
            if pic.type in _COVER_TYPES:
                if not replace_existing:
                    raise ExistingArtworkError("The file already has embedded artwork.")
                continue
            keep.append(b64)
        keep.insert(0, base64.b64encode(_flac_picture(image).write()).decode("ascii"))
        audio.tags["metadata_block_picture"] = keep
        audio.save()
    else:
        raise UnsupportedFormatError(f"{type(audio).__name__} files are not supported for embedding.")


@dataclass
class EmbedResult:
    destination: Path
    image_hash: str
    audio_fingerprint: str
    backup_path: Path | None
    replaced_existing_artwork: bool


def safe_embed(source: Path, destination: Path, image: bytes, *, replace_existing: bool = False,
               backups: BackupManager | None = None, track_id: int | None = None,
               require_backup: bool = True) -> EmbedResult:
    """Copy *source* to a temp file, embed, verify, back up *destination*, replace it.

    ``source`` and ``destination`` may be the same file (in-place update).
    """
    source, destination = Path(source), Path(destination)
    if not supports_embedding(source):
        raise UnsupportedFormatError(f"{source.suffix or 'This'} files do not support embedded artwork here.")
    try:
        before_fp = audio_fingerprint(source)
        before_tags = tag_snapshot(source)
        had_art = read_embedded_artwork(source).present
    except MetadataError as exc:
        raise EmbedError(str(exc)) from exc
    if had_art and not replace_existing:
        raise ExistingArtworkError("The file already has embedded artwork; replacing it was not approved.")

    tmp = temp_path_beside(destination, "artwork")
    backup_path: Path | None = None
    try:
        shutil.copy2(source, tmp)
        embed_artwork(tmp, image, replace_existing=replace_existing)
        _verify(tmp, image, before_fp, before_tags)
        if destination.exists():
            if backups is None:
                if require_backup:
                    raise EmbedError("Refusing to overwrite a file without a backup location.")
            else:
                backup_path = backups.backup(destination, track_id=track_id, reason="artwork embed")
        replace_with_retry(tmp, destination)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # Re-check the file that is actually in place now.
    _verify(destination, image, before_fp, before_tags)
    return EmbedResult(destination, sha256_bytes(image), before_fp, backup_path, had_art)


def _verify(path: Path, image: bytes, fingerprint: str, tags: dict[str, str]) -> None:
    try:
        art = read_embedded_artwork(path)
        after_fp = audio_fingerprint(path)
        after_tags = tag_snapshot(path)
    except Exception as exc:
        raise VerificationError(f"The updated file could not be read back: {exc}") from exc
    if not art.present or art.hash != sha256_bytes(image):
        raise VerificationError("Embedded artwork could not be read back from the updated file.")
    if after_fp != fingerprint:
        raise VerificationError("Audio data changed while embedding artwork; the change was discarded.")
    if after_tags != tags:
        changed = sorted(set(tags.items()) ^ set(after_tags.items()))
        raise VerificationError(f"Existing metadata changed while embedding artwork: {changed[:3]}")


def file_hash(path: Path) -> str:
    return sha256_file(path)
