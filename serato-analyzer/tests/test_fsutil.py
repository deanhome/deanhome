import pytest

from serato_analyzer.fsutil import (
    UnsafePathError, atomic_write_bytes, copy_verified, safe_filename, safe_join, sha256_file, unique_path,
)


@pytest.mark.parametrize("raw, expected", [
    ("Track: Remix?.mp3", "Track_ Remix_.mp3"),
    ('A "B" <C>|D*.mp3', "A _B_ _C__D_.mp3"),
    ("CON", "_CON"),
    ("con.mp3", "_con.mp3"),
    ("LPT1.txt", "_LPT1.txt"),
    ("name. . ", "name"),
    ("..", "untitled"),
    ("", "untitled"),
    ("../../etc/passwd", ".._.._etc_passwd"),
    ("a\\b/c", "a_b_c"),
    ("tab\there", "tab_here"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_truncates_but_keeps_extension():
    name = safe_filename("é" * 300 + ".flac")
    assert name.endswith(".flac") and len(name.encode()) <= 200


def test_safe_join_blocks_traversal(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    assert safe_join(root, "House", "a.mp3") == root / "House" / "a.mp3"
    # ".." is sanitised to a plain name, so it cannot escape.
    escaped = safe_join(root, "..", "a.mp3")
    assert escaped == root / "untitled" / "a.mp3"


def test_safe_join_blocks_symlink_escape(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        safe_join(root, "link", "a.mp3")


def test_unique_path(tmp_path):
    p = tmp_path / "a.mp3"
    assert unique_path(p) == p
    p.write_bytes(b"x")
    assert unique_path(p) == tmp_path / "a (2).mp3"


def test_atomic_write_and_verified_copy(tmp_path):
    src = tmp_path / "src.bin"
    atomic_write_bytes(src, b"hello")
    assert src.read_bytes() == b"hello"
    dest = tmp_path / "sub dir" / "dest.bin"
    digest = copy_verified(src, dest)
    assert digest == sha256_file(dest)
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(".")]  # no temp files left


def test_unicode_paths(tmp_path):
    d = tmp_path / "Café Del Mar" / "Ñandú"
    d.mkdir(parents=True)
    f = d / "Beyoncé – Halo.mp3"
    atomic_write_bytes(f, b"x")
    assert f.read_bytes() == b"x"
