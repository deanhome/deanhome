"""Artwork Finder orchestration: assess, search, review, select.

Everything here is UI-independent and safe to call from a worker thread.
Nothing in this module modifies audio files; see :mod:`artwork.apply`.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..db import Database, utcnow
from ..metadata import TrackMetadata
from ..platforms import PlatformServices
from .http import DEFAULT_USER_AGENT, HttpClient, HttpError, Transport
from .images import CropRequired, ImageLimits, ImageStore, InvalidImageError, normalize_image, validate_image_bytes
from .matching import likely_unofficial, rank_candidates
from .models import ArtworkCandidate, ArtworkStatus, Confidence, ReviewStatus, SearchStatus, TrackQuery
from .providers import ArtworkProvider, ProviderError, ProviderResult, ProviderStatus, build_providers
from .providers.manual import MANUAL_FILE, MANUAL_URL, candidate_from_file, candidate_from_url, web_search_url

#: Hosts each provider may serve images from.  Manually supplied URLs are the
#: only other images ever downloaded.
PROVIDER_IMAGE_HOSTS = {
    "musicbrainz": ("coverartarchive.org", "archive.org"),
    "apple_music": ("mzstatic.com",),
}

NEEDS_ARTWORK = (ArtworkStatus.MISSING.value, ArtworkStatus.LOW_RESOLUTION.value,
                 ArtworkStatus.NON_SQUARE.value, ArtworkStatus.UNREADABLE.value)


class DownloadError(Exception):
    pass


@dataclass
class SearchSummary:
    track_id: int
    status: SearchStatus
    candidates: list[ArtworkCandidate] = field(default_factory=list)
    provider_results: list[ProviderResult] = field(default_factory=list)
    message: str = ""
    from_cache: bool = False


@dataclass
class SelectionResult:
    track_id: int
    ok: bool
    needs_crop: bool = False
    width: int | None = None
    height: int | None = None
    note: str = ""
    image_path: Path | None = None


def assess_artwork(md: TrackMetadata, settings: Settings) -> ArtworkStatus:
    art = md.artwork
    if not art.present:
        return ArtworkStatus.MISSING
    if not art.readable or not art.width or not art.height:
        return ArtworkStatus.UNREADABLE
    if min(art.width, art.height) < settings.artwork_min_acceptable_px:
        return ArtworkStatus.LOW_RESOLUTION
    if abs(art.width - art.height) / max(art.width, art.height) > 0.1:
        return ArtworkStatus.NON_SQUARE
    return ArtworkStatus.OK


def build_http_client(settings: Settings, transport: Transport | None = None, **kwargs) -> HttpClient:
    ua = DEFAULT_USER_AGENT
    if settings.musicbrainz_contact.strip():
        ua = ua[:-2] + f"; {settings.musicbrainz_contact.strip()} )"
    return HttpClient(transport=transport, user_agent=ua, timeout=settings.request_timeout_seconds, **kwargs)


class ArtworkService:
    def __init__(self, db: Database, settings: Settings, images: ImageStore,
                 http: HttpClient, providers: list[ArtworkProvider] | None = None) -> None:
        self.db = db
        self.settings = settings
        self.images = images
        self.http = http
        self.providers = providers if providers is not None else build_providers(settings, http)
        self._thumb_cache: OrderedDict[str, bytes] = OrderedDict()
        self._thumb_lock = threading.Lock()

    @classmethod
    def create(cls, platform: PlatformServices, db: Database, settings: Settings,
               transport: Transport | None = None) -> "ArtworkService":
        http = build_http_client(settings, transport)
        limits = ImageLimits(settings.artwork_max_download_bytes, settings.artwork_max_dimension_px,
                             settings.artwork_min_dimension_px)
        images = ImageStore(Path(platform.artwork_cache_dir()), db, limits)
        return cls(db, settings, images, http)

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.http.timeout = settings.request_timeout_seconds
        self.images.limits = ImageLimits(settings.artwork_max_download_bytes,
                                         settings.artwork_max_dimension_px,
                                         settings.artwork_min_dimension_px)
        self.providers = build_providers(settings, self.http)

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------
    def record_scan(self, track_id: int, md: TrackMetadata) -> ArtworkStatus:
        status = assess_artwork(md, self.settings)
        art = md.artwork
        self.db.update_artwork_state(
            track_id,
            has_embedded=int(art.present), embedded_hash=art.hash, embedded_width=art.width,
            embedded_height=art.height, embedded_mime=art.mime, artwork_status=status.value)
        return status

    def request_replacement(self, track_id: int, requested: bool = True) -> None:
        """The user explicitly wants this track's existing artwork replaced."""
        self.db.update_artwork_state(track_id, replace_requested=int(requested))

    def tracks_needing_artwork(self, track_ids: list[int] | None = None) -> list[int]:
        rows = self.db.artwork_rows(track_ids)
        return [r["id"] for r in rows
                if (r["artwork_status"] in NEEDS_ARTWORK or r["replace_requested"])
                and r["review_status"] not in (ReviewStatus.APPLIED.value,
                                               ReviewStatus.NO_ARTWORK_AVAILABLE.value)]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def provider_overview(self) -> list[tuple[str, str | None]]:
        return [(p.display_name, p.configuration_problem()) for p in self.providers]

    def search_track(self, track_id: int, force: bool = False) -> SearchSummary:
        rows = self.db.artwork_rows([track_id])
        if not rows:
            raise KeyError(f"Unknown track {track_id}")
        row = rows[0]
        query = TrackQuery.from_row(row)

        if self.settings.offline_mode:
            msg = "Offline mode is on; online artwork search is disabled."
            self.db.update_artwork_state(track_id, search_status=SearchStatus.OFFLINE.value, search_message=msg)
            return SearchSummary(track_id, SearchStatus.OFFLINE, message=msg)
        if not query.searchable:
            msg = ("Not enough metadata to search (needs artist and title, an ISRC, or a MusicBrainz "
                   "release ID). Use Search Web for Cover Art or choose an image manually.")
            self.db.update_artwork_state(track_id, search_status=SearchStatus.NO_CANDIDATES.value,
                                         search_message=msg, last_search_at=utcnow())
            return SearchSummary(track_id, SearchStatus.NO_CANDIDATES, message=msg)

        if not force and row["last_search_at"] and row["search_status"] in (
                SearchStatus.FOUND.value, SearchStatus.NO_CANDIDATES.value):
            existing = self.candidates(track_id)
            if existing or row["search_status"] == SearchStatus.NO_CANDIDATES.value:
                return SearchSummary(track_id, SearchStatus(row["search_status"]), existing,
                                     message=row["search_message"] or "", from_cache=True)

        self.db.update_artwork_state(track_id, search_status=SearchStatus.SEARCHING.value, last_error=None)
        results: list[ProviderResult] = []
        found: list[ArtworkCandidate] = []
        for provider in self.providers:
            problem = provider.configuration_problem()
            if problem:
                results.append(ProviderResult(provider.name, ProviderStatus.NOT_CONFIGURED, message=problem))
                continue
            key = f"{provider.name}:{query.cache_key()}"
            cached = None if force else self._cache_get(key)
            if cached is not None:
                found.extend(cached)
                results.append(ProviderResult(provider.name, ProviderStatus.OK, cached, "cached"))
                continue
            try:
                cands = provider.search(query)
            except ProviderError as exc:
                results.append(ProviderResult(provider.name, exc.status, message=str(exc)))
                continue
            self._cache_put(key, provider.name, cands)
            found.extend(cands)
            results.append(ProviderResult(provider.name, ProviderStatus.OK, cands))

        ranked = rank_candidates(query, found)
        self._store_candidates(track_id, ranked)
        ok_results = [r for r in results if r.status == ProviderStatus.OK]
        problems = [f"{r.provider}: {r.message}" for r in results if r.status != ProviderStatus.OK]

        if ranked:
            status = SearchStatus.FOUND
            message = f"{len(ranked)} candidate(s); best is {ranked[0].confidence.value} confidence."
        elif not ok_results and any(r.status in (ProviderStatus.OFFLINE,) for r in results):
            status = SearchStatus.OFFLINE
            message = "Could not reach the artwork services. Check your connection and retry."
        elif not ok_results:
            status = SearchStatus.FAILED
            message = "No artwork provider could be searched."
        else:
            status = SearchStatus.NO_CANDIDATES
            message = "No matching artwork was found."
            if likely_unofficial(query):
                message += (" This looks like a DJ edit or unofficial version, which often has no "
                            "public cover. Choose an image manually if you have one.")
        if problems:
            message += " " + " ".join(problems)

        self.db.update_artwork_state(
            track_id, search_status=status.value, search_message=message.strip(),
            last_search_at=utcnow(), providers_queried=[r.provider for r in results],
            last_error=None if status in (SearchStatus.FOUND, SearchStatus.NO_CANDIDATES) else message)

        if ranked and self.settings.auto_select_high_confidence:
            self._maybe_auto_select(track_id, row, ranked)
        return SearchSummary(track_id, status, ranked, results, message.strip())

    def _maybe_auto_select(self, track_id: int, row, ranked: list[ArtworkCandidate]) -> None:
        best = ranked[0]
        if best.confidence != Confidence.HIGH or row["review_status"] != ReviewStatus.PENDING.value:
            return
        if row["has_embedded"] and not (self.settings.allow_replace_existing_artwork and row["replace_requested"]):
            return  # never replace existing artwork without explicit permission
        try:
            self.select_candidate(track_id, best.db_id)
        except (DownloadError, InvalidImageError):
            pass

    # Search-result cache --------------------------------------------------
    def _cache_get(self, key: str) -> list[ArtworkCandidate] | None:
        row = self.db.query_one("SELECT payload, fetched_at FROM provider_cache WHERE key = ?", (key,))
        if not row:
            return None
        if time.time() - row["fetched_at"] > self.settings.search_cache_days * 86400:
            return None
        try:
            items = json.loads(row["payload"])
            return [_candidate_from_dict(d) for d in items]
        except (ValueError, TypeError):
            return None

    def _cache_put(self, key: str, provider: str, cands: list[ArtworkCandidate]) -> None:
        payload = json.dumps([_candidate_to_dict(c) for c in cands])
        self.db.execute("INSERT OR REPLACE INTO provider_cache (key, provider, payload, fetched_at) "
                        "VALUES (?, ?, ?, ?)", (key, provider, payload, time.time()))

    # Candidate storage ------------------------------------------------------
    def _store_candidates(self, track_id: int, ranked: list[ArtworkCandidate]) -> None:
        state = self.db.get_artwork_state(track_id)
        keep_id = state["selected_candidate_id"] if state else None
        now = utcnow()
        with self.db.transaction() as conn:
            urls = []
            for c in ranked:
                urls.append((c.provider, c.image_url))
                conn.execute(
                    """INSERT INTO artwork_candidates (track_id, provider, image_url, thumbnail_url,
                    full_image_url, width, height, release_id, release_title, release_artist,
                    recording_title, release_date, release_type, label, catalog_number, isrc,
                    source_url, attribution, license, score, confidence, reasons, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(track_id, provider, image_url) DO UPDATE SET
                    score=excluded.score, confidence=excluded.confidence, reasons=excluded.reasons,
                    release_title=excluded.release_title, release_artist=excluded.release_artist,
                    recording_title=excluded.recording_title, release_date=excluded.release_date""",
                    (track_id, c.provider, c.image_url, c.thumbnail_url, c.full_image_url, c.width,
                     c.height, c.release_id, c.release_title, c.release_artist, c.recording_title,
                     c.release_date, c.release_type, c.label, c.catalog_number, c.isrc, c.source_url,
                     c.attribution, c.license, c.score, c.confidence.value, json.dumps(c.reasons), now))
                c.db_id = conn.execute(
                    "SELECT id FROM artwork_candidates WHERE track_id=? AND provider=? AND image_url=?",
                    (track_id, c.provider, c.image_url)).fetchone()[0]
            # Drop stale automatic candidates (keep manual ones and the selected one).
            for old in conn.execute("SELECT id, provider, image_url FROM artwork_candidates WHERE track_id=?",
                                    (track_id,)).fetchall():
                if old["id"] == keep_id or old["provider"] in (MANUAL_URL, MANUAL_FILE):
                    continue
                if (old["provider"], old["image_url"]) not in urls:
                    conn.execute("DELETE FROM artwork_candidates WHERE id=?", (old["id"],))

    def candidates(self, track_id: int, include_rejected: bool = False) -> list[ArtworkCandidate]:
        rows = self.db.query("SELECT * FROM artwork_candidates WHERE track_id = ?", (track_id,))
        out = [_candidate_from_row(r) for r in rows if include_rejected or not r["rejected"]]
        out.sort(key=lambda c: (c.confidence.rank, c.score, c.width or 0), reverse=True)
        return out

    def get_candidate(self, candidate_id: int) -> ArtworkCandidate | None:
        row = self.db.query_one("SELECT * FROM artwork_candidates WHERE id = ?", (candidate_id,))
        return _candidate_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------
    def reject_candidate(self, candidate_id: int) -> None:
        self.db.execute("UPDATE artwork_candidates SET rejected = 1 WHERE id = ?", (candidate_id,))

    def skip(self, track_id: int) -> None:
        self.db.update_artwork_state(track_id, review_status=ReviewStatus.SKIPPED.value)

    def mark_no_artwork(self, track_id: int) -> None:
        self.db.update_artwork_state(track_id, review_status=ReviewStatus.NO_ARTWORK_AVAILABLE.value)

    def reset_review(self, track_id: int) -> None:
        self.db.update_artwork_state(
            track_id, review_status=ReviewStatus.PENDING.value, selected_candidate_id=None,
            selected_image_path=None, selected_image_hash=None, selected_source=None,
            download_status="none")

    def select_candidate(self, track_id: int, candidate_id: int, crop_mode: str | None = None,
                         crop_box: tuple[int, int, int] | None = None) -> SelectionResult:
        cand = self.get_candidate(candidate_id)
        if cand is None:
            raise KeyError(f"Unknown candidate {candidate_id}")
        self.db.update_artwork_state(track_id, download_status="downloading", last_error=None)
        try:
            if cand.provider == MANUAL_FILE:
                row = self.db.query_one("SELECT path FROM image_cache WHERE source_url = ?", (cand.image_url,))
                if not row:
                    raise DownloadError("The imported image is no longer in the cache; import it again.")
                data = Path(row["path"]).read_bytes()
                validate_image_bytes(data, self.images.limits)
            else:
                data = self.download_image(cand.provider, cand.image_url)
        except (DownloadError, InvalidImageError) as exc:
            self.db.update_artwork_state(track_id, download_status="failed", last_error=str(exc))
            raise
        info = validate_image_bytes(data, self.images.limits)
        self.db.execute("UPDATE artwork_candidates SET width = ?, height = ? WHERE id = ?",
                        (info.width, info.height, candidate_id))
        self.db.update_artwork_state(track_id, selected_candidate_id=candidate_id,
                                     selected_source=cand.provider)
        return self._prepare_selected(track_id, data, crop_mode, crop_box)

    def set_crop(self, track_id: int, crop_mode: str | None = None,
                 crop_box: tuple[int, int, int] | None = None) -> SelectionResult:
        """Re-normalise the selected candidate after the user chose a crop."""
        state = self.db.get_artwork_state(track_id)
        if not state or not state["selected_candidate_id"]:
            raise ValueError("No artwork selected for this track")
        cand = self.get_candidate(state["selected_candidate_id"])
        if cand.provider == MANUAL_FILE:
            row = self.db.query_one("SELECT path FROM image_cache WHERE source_url = ?", (cand.image_url,))
            data = Path(row["path"]).read_bytes()
        else:
            data = self.download_image(cand.provider, cand.image_url)
        return self._prepare_selected(track_id, data, crop_mode, crop_box)

    def _prepare_selected(self, track_id: int, data: bytes, crop_mode: str | None,
                          crop_box: tuple[int, int, int] | None) -> SelectionResult:
        try:
            norm = normalize_image(data, self.settings.artwork_target_px,
                                   crop_mode or self.settings.artwork_crop_mode, crop_box,
                                   self.settings.artwork_square_tolerance, self.settings.artwork_jpeg_quality)
        except CropRequired as exc:
            self.db.update_artwork_state(track_id, download_status="needs_crop",
                                         review_status=ReviewStatus.PENDING.value)
            return SelectionResult(track_id, False, True, exc.width, exc.height, str(exc))
        path, _info = self.images.add(norm.data)
        self.db.update_artwork_state(
            track_id, selected_image_path=str(path), selected_image_hash=norm.hash,
            download_status="downloaded", review_status=ReviewStatus.APPROVED.value, last_error=None)
        return SelectionResult(track_id, True, False, norm.width, norm.height, norm.note, path)

    def add_manual_url(self, track_id: int, url: str) -> ArtworkCandidate:
        cand = candidate_from_url(url)
        self._store_manual(track_id, cand)
        return cand

    def add_manual_file(self, track_id: int, path: Path) -> ArtworkCandidate:
        path = Path(path)
        size = path.stat().st_size
        if size > self.images.limits.max_bytes:
            raise InvalidImageError(f"{path.name} is larger than the {self.images.limits.max_bytes:,}-byte limit.")
        data = path.read_bytes()
        cand = candidate_from_file(path)
        _p, info = self.images.add(data, source_url=cand.image_url)
        cand.width, cand.height = info.width, info.height
        self._store_manual(track_id, cand)
        return cand

    def _store_manual(self, track_id: int, cand: ArtworkCandidate) -> None:
        cand.confidence = Confidence.LOW
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO artwork_candidates (track_id, provider, image_url, thumbnail_url, width,
                height, source_url, attribution, license, score, confidence, reasons, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(track_id, provider, image_url) DO UPDATE SET rejected = 0""",
                (track_id, cand.provider, cand.image_url, cand.thumbnail_url, cand.width, cand.height,
                 cand.source_url, cand.attribution, cand.license, 0.0, cand.confidence.value,
                 json.dumps(cand.reasons), utcnow()))
            cand.db_id = conn.execute(
                "SELECT id FROM artwork_candidates WHERE track_id=? AND provider=? AND image_url=?",
                (track_id, cand.provider, cand.image_url)).fetchone()[0]

    def web_search_url(self, track_id: int) -> str:
        row = self.db.artwork_rows([track_id])[0]
        return web_search_url(TrackQuery.from_row(row), self.settings.web_search_engine)

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------
    def url_allowed(self, provider: str, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return False
        if provider == MANUAL_URL:
            return True
        host = (parts.hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in PROVIDER_IMAGE_HOSTS.get(provider, ()))

    def download_image(self, provider: str, url: str) -> bytes:
        if not self.url_allowed(provider, url):
            raise DownloadError(f"Refusing to download from an unapproved source: {urlsplit(url).netloc}")
        cached = self.images.lookup_url(url)
        if cached:
            return cached[0].read_bytes()
        if self.settings.offline_mode:
            raise DownloadError("Offline mode is on; cannot download artwork.")
        try:
            resp = self.http.get(url, {"Accept": "image/jpeg, image/png"},
                                 max_bytes=self.images.limits.max_bytes)
        except HttpError as exc:
            raise DownloadError(f"Download failed: {exc}") from exc
        try:
            self.images.add(resp.body, source_url=url)
        except InvalidImageError as exc:
            raise DownloadError(f"The downloaded file is not a usable image: {exc}") from exc
        return resp.body

    def fetch_thumbnail(self, provider: str, url: str) -> bytes | None:
        """Small preview for the review UI (memory-cached, validated)."""
        if not url:
            return None
        with self._thumb_lock:
            if url in self._thumb_cache:
                self._thumb_cache.move_to_end(url)
                return self._thumb_cache[url]
        if url.startswith("file:"):
            return None
        if not self.url_allowed(provider, url) or self.settings.offline_mode:
            return None
        try:
            resp = self.http.get(url, {"Accept": "image/jpeg, image/png"}, max_bytes=3 * 1024 * 1024)
            validate_image_bytes(resp.body, ImageLimits(3 * 1024 * 1024, 4000, 16))
        except (HttpError, InvalidImageError):
            return None
        with self._thumb_lock:
            self._thumb_cache[url] = resp.body
            while len(self._thumb_cache) > 300:
                self._thumb_cache.popitem(last=False)
        return resp.body

    # ------------------------------------------------------------------
    # Cache maintenance
    # ------------------------------------------------------------------
    def clear_cache(self, images: bool = True, searches: bool = True) -> None:
        """Clear cached search results and/or images.  Music files are never touched."""
        if searches:
            self.db.execute("DELETE FROM provider_cache")
        if images:
            self.images.clear()
            with self._thumb_lock:
                self._thumb_cache.clear()
            # Selections pointed into the cache; they must be re-downloaded.
            self.db.execute("UPDATE artwork_state SET selected_image_path = NULL, download_status = 'none', "
                            "review_status = 'pending' WHERE review_status = 'approved'")

    def prune_cache(self) -> dict[str, int]:
        cutoff = time.time() - self.settings.search_cache_days * 86400
        before = self.db.query_one("SELECT COUNT(*) FROM provider_cache")[0]
        self.db.execute("DELETE FROM provider_cache WHERE fetched_at < ?", (cutoff,))
        after = self.db.query_one("SELECT COUNT(*) FROM provider_cache")[0]
        keep = {r["selected_image_hash"] for r in self.db.query(
            "SELECT selected_image_hash FROM artwork_state WHERE review_status = 'approved'")
            if r["selected_image_hash"]}
        removed = self.images.prune(self.settings.image_cache_days, keep)
        return {"searches": before - after, "images": removed}


# ---------------------------------------------------------------------------

def _candidate_to_dict(c: ArtworkCandidate) -> dict:
    d = asdict(c)
    d["confidence"] = c.confidence.value
    d.pop("db_id", None)
    return d


def _candidate_from_dict(d: dict) -> ArtworkCandidate:
    d = dict(d)
    d["confidence"] = Confidence(d.get("confidence", "low"))
    d.pop("db_id", None)
    return ArtworkCandidate(**d)


def _candidate_from_row(row) -> ArtworkCandidate:
    try:
        reasons = json.loads(row["reasons"] or "[]")
    except ValueError:
        reasons = []
    return ArtworkCandidate(
        provider=row["provider"], image_url=row["image_url"], thumbnail_url=row["thumbnail_url"],
        full_image_url=row["full_image_url"], width=row["width"], height=row["height"],
        release_id=row["release_id"], release_title=row["release_title"] or "",
        release_artist=row["release_artist"] or "", recording_title=row["recording_title"] or "",
        release_date=row["release_date"] or "", release_type=row["release_type"] or "",
        label=row["label"] or "", catalog_number=row["catalog_number"] or "", isrc=row["isrc"] or "",
        source_url=row["source_url"], attribution=row["attribution"] or "", license=row["license"] or "",
        score=row["score"] or 0.0, confidence=Confidence(row["confidence"] or "low"), reasons=reasons,
        db_id=row["id"])
