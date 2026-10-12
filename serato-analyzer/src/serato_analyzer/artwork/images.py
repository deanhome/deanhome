"""Image validation, the artwork image cache, and normalisation.

Images are validated by content (magic bytes + a full Pillow decode), never by
filename.  Cached files are named by their SHA-256, so nothing from a URL or a
remote server ever becomes part of a filesystem path.
"""

from __future__ import annotations

import io
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from ..db import Database
from ..fsutil import atomic_write_bytes, sha256_bytes

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}
_MAGIC = {b"\xff\xd8\xff": "JPEG", b"\x89PNG\r\n\x1a\n": "PNG"}


class InvalidImageError(ValueError):
    pass


class CropRequired(Exception):
    """The image is not square and the user should choose how to crop it."""

    def __init__(self, width: int, height: int):
        super().__init__(f"Image is {width} × {height}; choose how to make it square.")
        self.width, self.height = width, height


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 15 * 1024 * 1024
    max_dimension: int = 10_000
    min_dimension: int = 64


@dataclass
class ImageInfo:
    format: str
    mime: str
    width: int
    height: int
    size_bytes: int
    hash: str


def sniff_format(data: bytes) -> str | None:
    for magic, fmt in _MAGIC.items():
        if data.startswith(magic):
            return fmt
    return None


def validate_image_bytes(data: bytes, limits: ImageLimits = ImageLimits()) -> ImageInfo:
    if not data:
        raise InvalidImageError("The download was empty.")
    if len(data) > limits.max_bytes:
        raise InvalidImageError(f"Image is {len(data):,} bytes; the limit is {limits.max_bytes:,}.")
    sniffed = sniff_format(data)
    if sniffed is None:
        raise InvalidImageError("Not a JPEG or PNG image (unrecognised file signature).")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h) = im.format, im.size
                if w > limits.max_dimension or h > limits.max_dimension:
                    raise InvalidImageError(f"Image is {w} × {h}; the limit is {limits.max_dimension}px.")
                im.verify()
            # verify() leaves the image unusable; decode fully to catch truncation.
            with Image.open(io.BytesIO(data)) as im:
                im.load()
    except InvalidImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImageError(f"Image is unreasonably large: {exc}") from exc
    except Exception as exc:  # PIL raises many types for corrupt data
        raise InvalidImageError(f"Corrupt or unreadable image: {exc}") from exc
    if fmt not in ALLOWED_FORMATS or fmt != sniffed:
        raise InvalidImageError(f"Unsupported image format: {fmt}.")
    if w < limits.min_dimension or h < limits.min_dimension:
        raise InvalidImageError(f"Image is only {w} × {h}; too small to use as artwork.")
    return ImageInfo(fmt, ALLOWED_FORMATS[fmt], w, h, len(data), sha256_bytes(data))


@dataclass
class NormalizedImage:
    data: bytes
    width: int
    height: int
    source_width: int
    source_height: int
    cropped: bool
    padded: bool
    note: str
    hash: str


def normalize_image(data: bytes, target: int = 1000, crop_mode: str = "center",
                    crop_box: tuple[int, int, int] | None = None, square_tolerance: float = 0.02,
                    quality: int = 92) -> NormalizedImage:
    """Make a square JPEG of at most *target* pixels.

    * ``crop_mode``: ``"center"`` crops the long edge, ``"pad"`` letterboxes,
      ``"ask"`` raises :class:`CropRequired` for non-square images.
    * ``crop_box``: ``(left, top, size)`` square chosen by the user.
    * Images are never enlarged: a 600px source stays 600px.
    """
    with Image.open(io.BytesIO(data)) as src:
        im = ImageOps.exif_transpose(src)
        im.load()
    sw, sh = im.size
    cropped = padded = False
    if crop_box is not None:
        left, top, size = (int(v) for v in crop_box)
        if size <= 0 or left < 0 or top < 0 or left + size > sw or top + size > sh:
            raise ValueError(f"Crop box {crop_box} is outside the {sw} × {sh} image.")
        im = im.crop((left, top, left + size, top + size))
        cropped = True
    elif abs(sw - sh) / max(sw, sh) > square_tolerance:
        if crop_mode == "ask":
            raise CropRequired(sw, sh)
        if crop_mode == "pad":
            edge = max(sw, sh)
            canvas = Image.new("RGB", (edge, edge), (0, 0, 0))
            canvas.paste(_to_rgb(im), ((edge - sw) // 2, (edge - sh) // 2))
            im = canvas
            padded = True
        else:
            edge = min(sw, sh)
            left, top = (sw - edge) // 2, (sh - edge) // 2
            im = im.crop((left, top, left + edge, top + edge))
            cropped = True
    else:
        # Near-square: trim the odd pixel(s) rather than stretch.
        edge = min(sw, sh)
        if sw != sh:
            im = im.crop(((sw - edge) // 2, (sh - edge) // 2, (sw - edge) // 2 + edge, (sh - edge) // 2 + edge))

    im = _to_rgb(im)
    edge = im.size[0]
    if edge > target:
        im = im.resize((target, target), Image.Resampling.LANCZOS)
        note = f"Resized from {edge}px to {target}px."
    elif edge < target:
        note = (f"Source is {edge}px, smaller than the {target}px target; kept at {edge}px "
                "(not enlarged).")
    else:
        note = f"{edge}px."
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=False)
    out = buf.getvalue()
    return NormalizedImage(out, im.size[0], im.size[1], sw, sh, cropped, padded, note, sha256_bytes(out))


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB") if im.mode != "RGB" else im


class ImageStore:
    """Content-addressed artwork cache with a retention policy."""

    def __init__(self, root: Path, db: Database, limits: ImageLimits = ImageLimits()) -> None:
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.tmp_dir = self.root / "tmp"
        self.db = db
        self.limits = limits
        for d in (self.images_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def path_for_hash(self, digest: str, fmt: str) -> Path:
        ext = ".png" if fmt == "PNG" else ".jpg"
        return self.images_dir / digest[:2] / f"{digest}{ext}"

    def lookup(self, digest: str) -> Path | None:
        row = self.db.query_one("SELECT path FROM image_cache WHERE hash = ?", (digest,))
        if row and Path(row["path"]).is_file():
            self.db.execute("UPDATE image_cache SET last_used_at = ? WHERE hash = ?", (time.time(), digest))
            return Path(row["path"])
        return None

    def lookup_url(self, url: str) -> tuple[Path, ImageInfo] | None:
        row = self.db.query_one("SELECT * FROM image_cache WHERE source_url = ? ORDER BY fetched_at DESC", (url,))
        if row and Path(row["path"]).is_file():
            data = Path(row["path"]).read_bytes()
            try:
                return Path(row["path"]), validate_image_bytes(data, self.limits)
            except InvalidImageError:
                return None
        return None

    def add(self, data: bytes, source_url: str | None = None) -> tuple[Path, ImageInfo]:
        info = validate_image_bytes(data, self.limits)
        dest = self.path_for_hash(info.hash, info.format)
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(dest, data)
        now = time.time()
        self.db.execute(
            "INSERT INTO image_cache (hash, path, source_url, width, height, mime, size_bytes, fetched_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(hash) DO UPDATE SET "
            "last_used_at = excluded.last_used_at, source_url = COALESCE(excluded.source_url, image_cache.source_url), "
            "path = excluded.path",
            (info.hash, str(dest), source_url, info.width, info.height, info.mime, info.size_bytes, now, now))
        return dest, info

    def prune(self, max_age_days: float, keep_hashes: set[str] | None = None) -> int:
        """Delete cached images not used within *max_age_days*.  Returns the count."""
        cutoff = time.time() - max_age_days * 86400
        keep = keep_hashes or set()
        removed = 0
        for row in self.db.query("SELECT hash, path FROM image_cache WHERE last_used_at < ?", (cutoff,)):
            if row["hash"] in keep:
                continue
            Path(row["path"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM image_cache WHERE hash = ?", (row["hash"],))
            removed += 1
        self._clear_tmp()
        return removed

    def clear(self) -> None:
        """Remove every cached image.

        Never touches music files, embedded artwork, or artwork the user saved
        with Mode 1 (that lives outside the cache).
        """
        import shutil

        for d in (self.images_dir, self.tmp_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        self.db.execute("DELETE FROM image_cache")

    def _clear_tmp(self) -> None:
        for f in self.tmp_dir.glob("*"):
            if f.is_file() and time.time() - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)
