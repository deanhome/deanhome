import io

import pytest
from PIL import Image

from conftest import make_image
from serato_analyzer.artwork.images import (
    CropRequired, ImageLimits, ImageStore, InvalidImageError, normalize_image, validate_image_bytes,
)
from serato_analyzer.db import Database


def test_valid_jpeg_and_png():
    assert validate_image_bytes(make_image(800, 800)).format == "JPEG"
    info = validate_image_bytes(make_image(640, 640, "PNG"))
    assert info.format == "PNG" and info.mime == "image/png" and info.width == 640


@pytest.mark.parametrize("data, msg", [
    (b"", "empty"),
    (b"<html><body>Not found</body></html>", "unrecognised"),
    (b"GIF89a" + b"\x00" * 100, "unrecognised"),
    (b"\xff\xd8\xff" + b"\x00" * 200, "Corrupt"),
])
def test_invalid_images_rejected(data, msg):
    with pytest.raises(InvalidImageError, match=msg):
        validate_image_bytes(data)


def test_truncated_jpeg_rejected():
    data = make_image(800, 800)
    with pytest.raises(InvalidImageError):
        validate_image_bytes(data[: len(data) // 2])


def test_png_with_jpeg_extension_content_is_what_counts():
    # A PNG is a PNG regardless of what a URL or filename claims.
    assert validate_image_bytes(make_image(300, 300, "PNG")).format == "PNG"


def test_webp_and_gif_unsupported():
    for fmt in ("WEBP", "GIF"):
        buf = io.BytesIO()
        Image.new("RGB", (300, 300)).save(buf, fmt)
        with pytest.raises(InvalidImageError):
            validate_image_bytes(buf.getvalue())


def test_limits():
    with pytest.raises(InvalidImageError, match="limit"):
        validate_image_bytes(make_image(500, 500), ImageLimits(max_bytes=100))
    with pytest.raises(InvalidImageError, match="limit"):
        validate_image_bytes(make_image(2100, 300), ImageLimits(max_dimension=2000))
    with pytest.raises(InvalidImageError, match="too small"):
        validate_image_bytes(make_image(40, 40))


def test_normalize_downscales_to_target():
    n = normalize_image(make_image(1600, 1600), target=1000)
    assert (n.width, n.height) == (1000, 1000)
    assert validate_image_bytes(n.data).format == "JPEG"


def test_normalize_never_enlarges():
    n = normalize_image(make_image(600, 600), target=1000)
    assert (n.width, n.height) == (600, 600)
    assert "not enlarged" in n.note


def test_non_square_asks_by_default_in_ask_mode():
    with pytest.raises(CropRequired) as exc:
        normalize_image(make_image(1600, 900), crop_mode="ask")
    assert (exc.value.width, exc.value.height) == (1600, 900)


def test_center_crop_and_pad_preserve_aspect():
    c = normalize_image(make_image(1600, 900), target=1000, crop_mode="center")
    assert (c.width, c.height) == (900, 900) and c.cropped
    p = normalize_image(make_image(1600, 900), target=1000, crop_mode="pad")
    assert (p.width, p.height) == (1000, 1000) and p.padded


def test_custom_crop_box():
    n = normalize_image(make_image(1600, 900), target=1000, crop_box=(700, 0, 900))
    assert (n.width, n.height) == (900, 900)
    with pytest.raises(ValueError):
        normalize_image(make_image(1600, 900), crop_box=(800, 0, 900))


def test_near_square_is_trimmed_not_stretched():
    n = normalize_image(make_image(1000, 990), target=1000, crop_mode="ask")
    assert (n.width, n.height) == (990, 990)


def test_transparent_png_flattened():
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    n = normalize_image(buf.getvalue(), target=1000)
    assert Image.open(io.BytesIO(n.data)).mode == "RGB"


def test_image_store_content_addressed_and_clearable(tmp_path):
    db = Database(":memory:")
    store = ImageStore(tmp_path / "cache", db)
    data = make_image(500, 500)
    p1, info = store.add(data, "https://coverartarchive.org/x.jpg")
    p2, _ = store.add(data, "https://coverartarchive.org/x.jpg")
    assert p1 == p2 and p1.name == f"{info.hash}.jpg"
    assert store.lookup_url("https://coverartarchive.org/x.jpg")[0] == p1
    with pytest.raises(InvalidImageError):
        store.add(b"<html>")
    store.clear()
    assert not p1.exists() and store.lookup(info.hash) is None


def test_image_store_prune_respects_retention(tmp_path):
    db = Database(":memory:")
    store = ImageStore(tmp_path / "cache", db)
    old, info_old = store.add(make_image(300, 300, color=(1, 2, 3)))
    keep, info_keep = store.add(make_image(300, 300, color=(9, 9, 9)))
    db.execute("UPDATE image_cache SET last_used_at = 0")
    assert store.prune(30, keep_hashes={info_keep.hash}) == 1
    assert not old.exists() and keep.exists()
