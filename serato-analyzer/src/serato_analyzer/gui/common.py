"""Small GUI helpers shared by the pages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap

from ..jobs import JobEvent

CONFIDENCE_COLORS = {"high": "#2e7d32", "medium": "#ef6c00", "low": "#c62828"}

LABELS = {
    # artwork status
    "unknown": "Unknown", "missing": "Missing", "low_resolution": "Low resolution",
    "non_square": "Possibly incorrect (not square)", "unreadable": "Unreadable", "ok": "OK",
    # search status
    "not_searched": "Not searched", "queued": "Queued", "searching": "Searching…",
    "found": "Candidates found", "no_candidates": "No match", "failed": "Failed", "offline": "Offline",
    # review status
    "pending": "Needs review", "approved": "Approved", "rejected": "Rejected", "skipped": "Skipped",
    "no_artwork_available": "No artwork available", "applied": "Applied",
    # providers
    "musicbrainz": "MusicBrainz / CAA", "apple_music": "Apple Music", "manual_url": "Image URL",
    "manual_file": "Image file",
}


def label(value: str | None) -> str:
    if not value:
        return ""
    return LABELS.get(value, value.replace("_", " ").capitalize())


def confidence_color(value: str | None) -> QColor:
    return QColor(CONFIDENCE_COLORS.get(value or "", "#757575"))


def image_from_bytes(data: bytes | None, size: int) -> QImage | None:
    if not data:
        return None
    img = QImage.fromData(data)
    if img.isNull():
        return None
    return img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class JobBridge(QObject):
    """Forwards job events from worker threads to the GUI thread."""

    event = Signal(object)

    def __call__(self, ev: JobEvent) -> None:
        self.event.emit(ev)


class ThumbnailLoader(QObject):
    """Loads thumbnails off the GUI thread; emits ``loaded(key, QImage)``."""

    loaded = Signal(str, object)

    def __init__(self, parent: QObject | None = None, workers: int = 4) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumbs")
        self._cache: dict[str, QPixmap] = {}
        self._pending: set[str] = set()

    def get(self, key: str, fetch: Callable[[], bytes | None], size: int = 64) -> QPixmap | None:
        if key in self._cache:
            return self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            self._pool.submit(self._work, key, fetch, size)
        return None

    def _work(self, key: str, fetch: Callable[[], bytes | None], size: int) -> None:
        try:
            img = image_from_bytes(fetch(), size)
        except Exception:
            img = None
        self.loaded.emit(key, img)

    def store(self, key: str, img: QImage | None) -> QPixmap | None:
        """Call from the GUI thread when ``loaded`` fires."""
        self._pending.discard(key)
        pix = QPixmap.fromImage(img) if img is not None else QPixmap()
        self._cache[key] = pix
        return pix

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
