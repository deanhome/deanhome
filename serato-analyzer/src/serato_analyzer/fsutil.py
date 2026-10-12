"""Platform-independent file helpers: safe names, safe joins, hashing, atomic replace.

Filenames are always sanitised to the strictest common rules (Windows), because
music on an exFAT/NTFS external drive routinely moves between a Mac and a PC.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path

WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
MAX_NAME_BYTES = 200


class UnsafePathError(ValueError):
    """A computed path would escape the directory it must stay inside."""


def safe_filename(name: str, default: str = "untitled", max_bytes: int = MAX_NAME_BYTES) -> str:
    """Return a filename that is valid on macOS and Windows and cannot traverse."""
    name = unicodedata.normalize("NFC", str(name))
    name = _INVALID_CHARS.sub("_", name).strip()
    # Windows silently strips trailing dots and spaces, which can alias names.
    # This also turns "." and ".." into "".
    name = name.rstrip(". ")
    if not name:
        name = default
    if name.partition(".")[0].upper().rstrip() in WINDOWS_RESERVED_NAMES:
        name = "_" + name
    if len(name.encode("utf-8")) > max_bytes:
        base, ext = os.path.splitext(name)
        ext = ext if len(ext.encode("utf-8")) <= 16 else ""
        budget = max_bytes - len(ext.encode("utf-8"))
        base = base.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(". ")
        name = (base or default) + ext
    return name


def ensure_within(root: Path, candidate: Path) -> Path:
    """Raise :class:`UnsafePathError` unless *candidate* resolves inside *root*."""
    root_r = Path(root).resolve()
    cand_r = Path(candidate).resolve()
    if cand_r != root_r and root_r not in cand_r.parents:
        raise UnsafePathError(f"{candidate} is outside {root}")
    return candidate


def safe_join(root: Path, *parts: str) -> Path:
    """Join sanitised *parts* under *root*, refusing anything that escapes it."""
    clean = [safe_filename(p) for p in parts if p not in ("", ".")]
    candidate = Path(root).joinpath(*clean)
    return ensure_within(root, candidate)


def unique_path(path: Path) -> Path:
    """Return *path*, or ``name (2).ext`` etc. if it already exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10_000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free name for {path}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_with_retry(src: Path, dest: Path, attempts: int = 5, delay: float = 0.2) -> None:
    """``os.replace`` with retries.

    On Windows a file can be briefly locked by antivirus scanners, the search
    indexer or a DJ application, which surfaces as PermissionError.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def temp_path_beside(dest: Path, tag: str = "tmp") -> Path:
    """A unique temporary file in *dest*'s directory (same volume => atomic replace)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{dest.stem[:40]}.", suffix=f".{tag}{dest.suffix}",
                                dir=dest.parent)
    os.close(fd)
    return Path(name)


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    tmp = temp_path_beside(dest)
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def copy_verified(src: Path, dest: Path) -> str:
    """Copy *src* to *dest* atomically, verify the hash, and return it."""
    expected = sha256_file(src)
    tmp = temp_path_beside(dest)
    try:
        shutil.copy2(src, tmp)
        if sha256_file(tmp) != expected:
            raise OSError(f"Copy of {src} did not verify")
        replace_with_retry(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return expected


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total
