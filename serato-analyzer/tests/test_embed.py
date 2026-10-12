"""Embedding preserves audio and metadata, refuses silent overwrites, and backs up."""

import shutil

import pytest

from conftest import make_aiff, make_flac, make_image, make_m4a, make_mp3, make_ogg, make_wav
from serato_analyzer.artwork import embed as embed_mod
from serato_analyzer.artwork.backups import BackupError, BackupManager
from serato_analyzer.artwork.embed import (
    EmbedError, ExistingArtworkError, UnsupportedFormatError, VerificationError, audio_fingerprint,
    embed_artwork, safe_embed, tag_snapshot,
)
from serato_analyzer.db import Database
from serato_analyzer.fsutil import sha256_file
from serato_analyzer.metadata import read_metadata

MAKERS = {
    "mp3": lambda p: make_mp3(p, {"artist": "Disclosure", "title": "Latch", "album": "Settle",
                                  "comment": "energy 8"}),
    "mp3v24": lambda p: make_mp3(p, {"artist": "Disclosure", "title": "Latch"}, v2_version=4),
    "wav": lambda p: make_wav(p, {"artist": "Disclosure", "title": "Latch"}),
    "aiff": lambda p: make_aiff(p, {"artist": "Disclosure", "title": "Latch"}),
    "flac": lambda p: make_flac(p, {"artist": "Disclosure", "title": "Latch", "label": "PMR"}),
    "m4a": lambda p: make_m4a(p, {"artist": "Disclosure", "title": "Latch", "album": "Settle"}),
    "ogg": lambda p: make_ogg(p, {"artist": "Disclosure", "title": "Latch"}),
}
EXT = {"mp3v24": "mp3"}


@pytest.fixture
def backups(tmp_path):
    return BackupManager(tmp_path / "backups", Database(":memory:"))


@pytest.mark.parametrize("kind", list(MAKERS))
def test_embed_preserves_audio_and_tags(tmp_path, kind, backups):
    src = MAKERS[kind](tmp_path / f"track.{EXT.get(kind, kind)}")
    original_hash = sha256_file(src)
    fp, tags = audio_fingerprint(src), tag_snapshot(src)
    dest = tmp_path / "out" / src.name
    art = make_image(1000, 1000)

    result = safe_embed(src, dest, art, backups=backups)

    assert sha256_file(src) == original_hash, "source must be untouched"
    md = read_metadata(dest)
    assert md.artwork.present and md.artwork.width == 1000
    assert md.artist == "Disclosure" and md.title == "Latch"
    assert audio_fingerprint(dest) == fp
    assert tag_snapshot(dest) == tags
    assert result.backup_path is None  # nothing existed at dest
    assert [p.name for p in dest.parent.iterdir()] == [src.name]  # no temp files left behind


def test_fingerprint_detects_audio_change(tmp_path):
    a = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"})
    fp = audio_fingerprint(a)
    data = bytearray(a.read_bytes())
    data[-10] ^= 0xFF
    a.write_bytes(bytes(data))
    assert audio_fingerprint(a) != fp


def test_mp3_id3_version_preserved(tmp_path):
    from mutagen.id3 import ID3

    for v in (3, 4):
        p = make_mp3(tmp_path / f"v{v}.mp3", {"artist": "A", "title": "T"}, v2_version=v)
        embed_artwork(p, make_image(500, 500))
        assert ID3(p).version[1] == v


def test_existing_artwork_never_silently_replaced(tmp_path, backups):
    old_art = make_image(500, 500, color=(0, 0, 255))
    src = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"}, artwork=old_art)
    with pytest.raises(ExistingArtworkError):
        safe_embed(src, tmp_path / "out.mp3", make_image(800, 800), backups=backups)
    with pytest.raises(ExistingArtworkError):
        embed_artwork(src, make_image(800, 800))
    assert read_metadata(src).artwork.data == old_art


@pytest.mark.parametrize("maker", [
    lambda p: make_mp3(p.with_suffix(".mp3"), {"artist": "A", "title": "T"}, artwork=make_image(300, 300)),
    lambda p: make_flac(p.with_suffix(".flac"), {"artist": "A"}, artwork=make_image(300, 300)),
    lambda p: make_m4a(p.with_suffix(".m4a"), {"artist": "A"}, artwork=make_image(300, 300)),
])
def test_explicit_replacement(tmp_path, maker, backups):
    src = maker(tmp_path / "t")
    new = make_image(900, 900, color=(0, 200, 0))
    safe_embed(src, src, new, replace_existing=True, backups=backups)
    art = read_metadata(src).artwork
    assert art.width == 900 and art.hash == embed_mod.sha256_bytes(new)


def test_unsupported_format(tmp_path, backups):
    p = tmp_path / "track.wma"
    p.write_bytes(b"0&\xb2u\x8ef\xcf\x11" + b"\x00" * 100)
    with pytest.raises(UnsupportedFormatError):
        safe_embed(p, tmp_path / "x.wma", make_image(), backups=backups)
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    with pytest.raises(UnsupportedFormatError):
        embed_artwork(txt, make_image())


def test_corrupt_audio_is_reported(tmp_path, backups):
    p = tmp_path / "broken.flac"
    p.write_bytes(b"not really flac")
    with pytest.raises(EmbedError):
        safe_embed(p, tmp_path / "o.flac", make_image(), backups=backups)


def test_in_place_backs_up_and_restores(tmp_path):
    db = Database(":memory:")
    mgr = BackupManager(tmp_path / "backups", db)
    src = make_flac(tmp_path / "a.flac", {"artist": "A", "title": "T"})
    original = src.read_bytes()

    result = safe_embed(src, src, make_image(800, 800), backups=mgr, track_id=None)
    assert result.backup_path and result.backup_path.read_bytes() == original
    assert read_metadata(src).artwork.present

    backup_id = mgr.list()[0]["id"]
    mgr.restore(backup_id)
    assert src.read_bytes() == original
    assert mgr.list()[0]["restored_at"]


def test_damaged_backup_refused(tmp_path):
    db = Database(":memory:")
    mgr = BackupManager(tmp_path / "backups", db)
    src = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"})
    bpath = mgr.backup(src)
    bpath.write_bytes(b"tampered")
    with pytest.raises(BackupError, match="damaged"):
        mgr.restore(mgr.list()[0]["id"])


def test_overwrite_requires_backup_location(tmp_path):
    src = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"})
    with pytest.raises(EmbedError, match="backup"):
        safe_embed(src, src, make_image(), backups=None)


def test_failed_verification_leaves_destination_untouched(tmp_path, backups, monkeypatch):
    src = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"})
    dest = tmp_path / "dest.mp3"
    shutil.copy(src, dest)
    before = dest.read_bytes()

    real = embed_mod.embed_artwork

    def corrupting(path, image, replace_existing=False):
        real(path, image, replace_existing)
        data = bytearray(path.read_bytes())
        data[-5] ^= 0xFF  # simulate a writer that damaged the audio
        path.write_bytes(bytes(data))

    monkeypatch.setattr(embed_mod, "embed_artwork", corrupting)
    with pytest.raises(VerificationError, match="Audio data changed"):
        safe_embed(src, dest, make_image(), backups=backups)
    assert dest.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["a.mp3", "dest.mp3"]


def test_metadata_change_detected(tmp_path, backups, monkeypatch):
    src = make_mp3(tmp_path / "a.mp3", {"artist": "A", "title": "T"})
    real = embed_mod.embed_artwork

    def retitling(path, image, replace_existing=False):
        real(path, image, replace_existing)
        from mutagen.id3 import ID3, TIT2
        tags = ID3(path)
        tags.add(TIT2(encoding=3, text="Changed"))
        tags.save(path, v2_version=3)

    monkeypatch.setattr(embed_mod, "embed_artwork", retitling)
    with pytest.raises(VerificationError, match="metadata changed"):
        safe_embed(src, tmp_path / "o.mp3", make_image(), backups=backups)
