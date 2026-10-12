"""Shared fixtures.  Audio files are synthesised byte-by-byte so the tests need
no encoder, no network and no sample library."""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from serato_analyzer.artwork.http import HttpClient, NetworkError, Response  # noqa: E402
from serato_analyzer.config import Settings  # noqa: E402
from serato_analyzer.context import AppContext  # noqa: E402
from serato_analyzer.platforms import LinuxPlatform  # noqa: E402


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def make_image(width: int = 1200, height: int = 1200, fmt: str = "JPEG",
               color: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    im = Image.new("RGB", (width, height), color)
    # A little structure so JPEG output differs for different inputs.
    for x in range(0, width, max(1, width // 8)):
        for y in range(height):
            im.putpixel((x, y), (255 - color[0], color[1], 255 - color[2]))
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

# MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding, stereo => 417-byte frames.
_MP3_HEADER = b"\xff\xfb\x90\x64"
_MP3_FRAME = _MP3_HEADER + bytes(range(256)) + bytes(417 - 4 - 256)


def _audio_frames(n: int = 40) -> bytes:
    return _MP3_FRAME * n


def make_mp3(path: Path, tags: dict | None = None, artwork: bytes | None = None,
             v2_version: int = 3) -> Path:
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TSRC, TXXX, TDRC

    path.write_bytes(_audio_frames())
    tags = tags or {}
    if tags or artwork:
        id3 = ID3()
        if "artist" in tags:
            id3.add(TPE1(encoding=3, text=tags["artist"]))
        if "title" in tags:
            id3.add(TIT2(encoding=3, text=tags["title"]))
        if "album" in tags:
            id3.add(TALB(encoding=3, text=tags["album"]))
        if "year" in tags:
            id3.add(TDRC(encoding=3, text=tags["year"]))
        if "isrc" in tags:
            id3.add(TSRC(encoding=3, text=tags["isrc"]))
        if "mb_release_id" in tags:
            id3.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=tags["mb_release_id"]))
        if "comment" in tags:
            id3.add(TXXX(encoding=3, desc="DJ NOTE", text=tags["comment"]))
        if artwork:
            id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=artwork))
        id3.save(path, v2_version=v2_version)
    return path


def make_wav(path: Path, tags: dict | None = None) -> Path:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(bytes(range(256)) * 40)
    if tags:
        from mutagen.wave import WAVE
        from mutagen.id3 import TIT2, TPE1

        f = WAVE(path)
        f.add_tags()
        f.tags.add(TPE1(encoding=3, text=tags.get("artist", "")))
        f.tags.add(TIT2(encoding=3, text=tags.get("title", "")))
        f.save()
    return path


def make_aiff(path: Path, tags: dict | None = None) -> Path:
    frames = 2000
    sound = bytes(range(256)) * ((frames * 4) // 256 + 1)
    sound = sound[: frames * 4]
    # 80-bit IEEE extended for 44100.0
    rate = b"\x40\x0e\xac\x44" + b"\x00" * 6
    comm = struct.pack(">hIh", 2, frames, 16) + rate
    ssnd = struct.pack(">II", 0, 0) + sound
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    if tags:
        from mutagen.aiff import AIFF
        from mutagen.id3 import TIT2, TPE1

        f = AIFF(path)
        f.add_tags()
        f.tags.add(TPE1(encoding=3, text=tags.get("artist", "")))
        f.tags.add(TIT2(encoding=3, text=tags.get("title", "")))
        f.save()
    return path


def make_flac(path: Path, tags: dict | None = None, artwork: bytes | None = None) -> Path:
    # STREAMINFO: min/max block 4096, frame sizes 0, 44100 Hz, 2 ch, 16 bit, 44100 samples
    sample_rate, channels, bps, total = 44100, 2, 16, 44100
    packed = (sample_rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00" * 2 + packed.to_bytes(8, "big") + b"\x00" * 16
    header = bytes([0x80]) + len(streaminfo).to_bytes(3, "big")  # last block
    fake_frames = b"\xff\xf8" + bytes(range(256)) * 20
    path.write_bytes(b"fLaC" + header + streaminfo + fake_frames)
    from mutagen.flac import FLAC, Picture

    f = FLAC(path)
    if tags:
        if f.tags is None:
            f.add_tags()
        for k, v in tags.items():
            f.tags[k] = v
    if artwork:
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", artwork
        f.add_picture(pic)
    f.save()
    return path


def make_m4a(path: Path, tags: dict | None = None, artwork: bytes | None = None) -> Path:
    def atom(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    ftyp = atom(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
    mvhd = atom(b"mvhd", b"\x00" * 4 + struct.pack(">IIII", 0, 0, 1000, 5000) + b"\x00" * 80)
    moov = atom(b"moov", mvhd)
    mdat = atom(b"mdat", bytes(range(256)) * 30)
    path.write_bytes(ftyp + moov + mdat)
    from mutagen.mp4 import MP4, MP4Cover

    f = MP4(path)
    if f.tags is None:
        f.add_tags()
    tags = tags or {}
    if "artist" in tags:
        f.tags["\xa9ART"] = [tags["artist"]]
    if "title" in tags:
        f.tags["\xa9nam"] = [tags["title"]]
    if "album" in tags:
        f.tags["\xa9alb"] = [tags["album"]]
    if artwork:
        f.tags["covr"] = [MP4Cover(artwork, MP4Cover.FORMAT_JPEG)]
    f.save()
    return path


def make_ogg(path: Path, tags: dict | None = None) -> Path:
    from mutagen.ogg import OggPage

    ident = (b"\x01vorbis" + struct.pack("<IBIiiiB", 0, 2, 44100, 0, 128000, 0, 0xB8) + b"\x01")
    comment = b"\x03vorbis" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0) + b"\x01"
    setup = b"\x05vorbis" + bytes(range(64))
    audio_packets = [bytes([i % 256]) * 300 for i in range(20)]
    serial = 1234
    pages = []

    def page(packets, seq, position, first=False, last=False):
        p = OggPage()
        p.serial, p.sequence, p.position = serial, seq, position
        p.packets = packets
        p.first = first
        p.last = last
        return p

    pages.append(page([ident], 0, 0, first=True))
    pages.append(page([comment, setup], 1, 0))
    for i, pkt in enumerate(audio_packets):
        pages.append(page([pkt], 2 + i, (i + 1) * 1024, last=i == len(audio_packets) - 1))
    path.write_bytes(b"".join(p.write() for p in pages))
    if tags:
        from mutagen.oggvorbis import OggVorbis

        f = OggVorbis(path)
        for k, v in tags.items():
            f.tags[k] = v
        f.save()
    return path


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class FakeTransport:
    """Maps URL substrings to canned responses (or exceptions)."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, Callable[[str], Response] | Response | Exception]] = []
        self.calls: list[str] = []

    def add(self, fragment: str, response) -> None:
        self.routes.insert(0, (fragment, response))

    def add_json(self, fragment: str, payload, status: int = 200, headers=None) -> None:
        self.add(fragment, Response(status, json.dumps(payload).encode(), fragment, headers or {}))

    def add_bytes(self, fragment: str, body: bytes, status: int = 200) -> None:
        self.add(fragment, Response(status, body, fragment, {"Content-Type": "image/jpeg"}))

    def get(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        for fragment, resp in self.routes:
            if fragment in url:
                if isinstance(resp, Exception):
                    raise resp
                if callable(resp):
                    resp = resp(url)
                if len(resp.body) > max_bytes:
                    from serato_analyzer.artwork.http import ResponseTooLarge

                    raise ResponseTooLarge("too large")
                return Response(resp.status, resp.body, url, resp.headers)
        return Response(404, b"{}", url, {})


class OfflineTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        raise NetworkError("Network is unreachable")


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def fake_clock():
    class Clock:
        def __init__(self):
            self.now = 1000.0
            self.sleeps: list[float] = []

        def time(self):
            return self.now

        def sleep(self, s):
            self.sleeps.append(s)
            self.now += s

    return Clock()


@pytest.fixture
def ctx(tmp_path, transport, fake_clock):
    """An AppContext with isolated folders, a fake network and no real sleeping."""
    home = tmp_path / "home"
    env = {"XDG_CONFIG_HOME": str(home / "cfg"), "XDG_DATA_HOME": str(home / "data"),
           "XDG_CACHE_HOME": str(home / "cache"), "XDG_STATE_HOME": str(home / "state")}
    platform = LinuxPlatform(home=home, env=env)
    settings = Settings(output_dir=str(tmp_path / "out"), artwork_crop_mode="center")
    c = AppContext(platform=platform, settings=settings, transport=transport)
    c.artwork.http = HttpClient(transport, clock=fake_clock.time, sleep=fake_clock.sleep)
    c.artwork.apply_settings(settings)
    yield c
    c.close()


@pytest.fixture
def music(tmp_path) -> Path:
    d = tmp_path / "DJ Music"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# MusicBrainz / Cover Art Archive mocks
# ---------------------------------------------------------------------------

REL_SETTLE = "11111111-1111-4111-8111-111111111111"
REL_SINGLE = "22222222-2222-4222-8222-222222222222"
REL_REMIX = "33333333-3333-4333-8333-333333333333"
REL_OTHER = "44444444-4444-4444-8444-444444444444"


def mb_recording(title, artist, releases, isrcs=(), disambiguation=""):
    return {"id": "rec", "title": title, "disambiguation": disambiguation,
            "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"name": artist}}],
            "isrcs": list(isrcs), "releases": releases}


def mb_release(rid, title, date="2013-05-31", rtype="Album", status="Official"):
    return {"id": rid, "title": title, "date": date, "status": status,
            "release-group": {"primary-type": rtype}}


def caa_payload(rid):
    base = f"http://coverartarchive.org/release/{rid}"
    return {"images": [{"front": True, "types": ["Front"], "image": f"{base}/front.jpg",
                        "thumbnails": {"250": f"{base}/front-250.jpg", "500": f"{base}/front-500.jpg",
                                       "1200": f"{base}/front-1200.jpg"}}]}


def install_musicbrainz(transport, recordings, covers: dict[str, bytes] | None = None,
                        isrc_recordings=None):
    """Register search + CAA + image routes.  ``covers`` maps release id -> image bytes."""
    transport.add_json("musicbrainz.org/ws/2/recording", {"recordings": recordings})
    if isrc_recordings is not None:
        transport.add_json("musicbrainz.org/ws/2/isrc/", {"recordings": isrc_recordings})
    for rid, img in (covers or {}).items():
        transport.add_json(f"coverartarchive.org/release/{rid}", caa_payload(rid))
        transport.add_bytes(f"coverartarchive.org/release/{rid}/front-1200.jpg", img)
        transport.add_bytes(f"coverartarchive.org/release/{rid}/front-250.jpg", make_image(250, 250))
