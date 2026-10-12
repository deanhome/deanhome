"""User settings, stored as JSON in the platform's config directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .fsutil import atomic_write_bytes

APPLE_MUSIC_TOKEN_ENV = "SERATO_ANALYZER_APPLE_MUSIC_TOKEN"


@dataclass
class Settings:
    # Where Mode 3 (and copy-based Mode 2) writes prepared tracks. Empty means
    # the platform default (<Music>/Serato_Analyzed_Music).
    output_dir: str = ""

    # Artwork assessment
    artwork_min_acceptable_px: int = 500      # below this, artwork is "low resolution"
    artwork_square_tolerance: float = 0.02    # aspect-ratio deviation considered square

    # Normalisation
    artwork_target_px: int = 1000
    artwork_crop_mode: str = "ask"            # ask | center | pad
    artwork_jpeg_quality: int = 92

    # Download safety limits
    artwork_max_download_bytes: int = 15 * 1024 * 1024
    artwork_max_dimension_px: int = 10_000
    artwork_min_dimension_px: int = 64
    request_timeout_seconds: float = 15.0

    # Providers
    offline_mode: bool = False
    provider_musicbrainz: bool = True
    provider_apple_music: bool = False
    apple_music_storefront: str = "us"
    apple_music_developer_token: str = ""
    musicbrainz_contact: str = ""             # e-mail/URL added to the User-Agent
    web_search_engine: str = "duckduckgo"     # duckduckgo | google | bing

    # Cache retention
    search_cache_days: int = 30
    image_cache_days: int = 90

    # Safety switches (all off by default)
    auto_select_high_confidence: bool = False
    allow_replace_existing_artwork: bool = False
    allow_modify_originals: bool = False
    save_artwork_alongside_music: bool = False

    extra: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def apple_music_token(self) -> str:
        return self.apple_music_developer_token or os.environ.get(APPLE_MUSIC_TOKEN_ENV, "")

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            # A corrupt settings file must not stop the app from starting.
            return cls()
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        settings = cls(**values)
        settings.validate()
        return settings

    def save(self, path: Path) -> None:
        self.validate()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(Path(path), json.dumps(asdict(self), indent=2).encode("utf-8"))

    def validate(self) -> None:
        if self.artwork_crop_mode not in ("ask", "center", "pad"):
            self.artwork_crop_mode = "ask"
        self.artwork_target_px = max(100, min(int(self.artwork_target_px), 5000))
        self.artwork_min_acceptable_px = max(50, int(self.artwork_min_acceptable_px))
        self.apple_music_storefront = (self.apple_music_storefront or "us").strip().lower()[:8] or "us"
        if self.web_search_engine not in ("duckduckgo", "google", "bing"):
            self.web_search_engine = "duckduckgo"
