"""Data types shared by artwork providers, matching, the service and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"high": 3, "medium": 2, "low": 1}[self.value]


class ArtworkStatus(str, Enum):
    UNKNOWN = "unknown"
    MISSING = "missing"
    LOW_RESOLUTION = "low_resolution"
    NON_SQUARE = "non_square"          # potentially incorrect (e.g. a photo or banner)
    UNREADABLE = "unreadable"          # embedded data is not a valid image
    OK = "ok"


class SearchStatus(str, Enum):
    NOT_SEARCHED = "not_searched"
    QUEUED = "queued"
    SEARCHING = "searching"
    FOUND = "found"
    NO_CANDIDATES = "no_candidates"
    FAILED = "failed"
    OFFLINE = "offline"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    NO_ARTWORK_AVAILABLE = "no_artwork_available"
    APPLIED = "applied"


class ApplyMode(str, Enum):
    SAVE_SEPARATELY = "save_separately"   # Mode 1: audio untouched
    EMBED = "embed"                       # Mode 2: embed into a file (copy or, if allowed, original)
    PREPARE_FOR_SERATO = "serato"         # Mode 3: copy into Serato_Analyzed_Music with art embedded


@dataclass
class TrackQuery:
    """The metadata a provider searches with."""

    artist: str = ""
    title: str = ""
    album: str = ""
    year: str = ""
    label: str = ""
    catalog_number: str = ""
    isrc: str = ""
    mb_release_id: str = ""
    track_id: int | None = None

    @classmethod
    def from_row(cls, row) -> "TrackQuery":
        keys = row.keys() if hasattr(row, "keys") else []

        def g(k: str) -> str:
            return (row[k] or "") if k in keys else ""

        return cls(artist=g("artist"), title=g("title"), album=g("album"), year=g("year"),
                   label=g("label"), catalog_number=g("catalog_number"), isrc=g("isrc"),
                   mb_release_id=g("mb_release_id"),
                   track_id=row["id"] if "id" in keys else None)

    def cache_key(self) -> str:
        from .matching import normalize

        parts = [normalize(self.artist), normalize(self.title), normalize(self.album),
                 self.isrc.upper().replace("-", ""), self.mb_release_id.lower()]
        return "|".join(parts)

    @property
    def searchable(self) -> bool:
        return bool(self.mb_release_id or self.isrc or (self.artist.strip() and self.title.strip()))


@dataclass
class ArtworkCandidate:
    provider: str
    image_url: str
    thumbnail_url: str | None = None
    full_image_url: str | None = None
    width: int | None = None
    height: int | None = None
    release_id: str | None = None
    release_title: str = ""
    release_artist: str = ""
    recording_title: str = ""
    release_date: str = ""
    release_type: str = ""
    label: str = ""
    catalog_number: str = ""
    isrc: str = ""
    source_url: str | None = None
    attribution: str = ""
    license: str = ""
    #: Set when the provider matched on an exact identifier ("isrc", "mb_release_id").
    identifier_match: str = ""
    score: float = 0.0
    confidence: Confidence = Confidence.LOW
    reasons: list[str] = field(default_factory=list)
    db_id: int | None = None

    @property
    def dimensions_text(self) -> str:
        if self.width and self.height:
            return f"{self.width} × {self.height}"
        return "size unknown"
