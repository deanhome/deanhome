"""Optional Apple Music catalog search.

Docs: https://developer.apple.com/documentation/AppleMusicAPI

Catalog requests need a *developer token* (a JWT signed with a MusicKit key
from an Apple Developer account).  The user supplies a token in Settings or
via the ``SERATO_ANALYZER_APPLE_MUSIC_TOKEN`` environment variable; without
one this provider reports that it is not configured and the search carries on
with the other providers.

Artwork URLs are templates (``.../{w}x{h}bb.jpg``); we request the largest
size the catalog reports, capped at 3000px.  Artwork is subject to Apple's
API terms and may only be used as those terms allow.
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import quote

from ..http import HttpClient, HttpError, NetworkError, RateLimitedError
from ..matching import parse_title
from ..models import ArtworkCandidate, TrackQuery
from .base import ArtworkProvider, ProviderError, ProviderStatus

API_HOST = "api.music.apple.com"
API_BASE = f"https://{API_HOST}/v1/catalog"
MAX_EDGE = 3000
ATTRIBUTION = "Apple Music"
LICENSE_NOTE = ("Artwork supplied by Apple Music; subject to the Apple Media Services and "
                "MusicKit terms. Use only as those terms permit.")
_STOREFRONT = re.compile(r"^[a-z]{2}$")


def artwork_url(template: str, width: int, height: int) -> str:
    return template.replace("{w}", str(width)).replace("{h}", str(height)).replace("{f}", "jpg")


class AppleMusicProvider(ArtworkProvider):
    name = "apple_music"
    display_name = "Apple Music"

    def __init__(self, http: HttpClient, token_source: Callable[[], str], storefront: str = "us") -> None:
        self.http = http
        self._token_source = token_source
        self.storefront = (storefront or "us").lower()
        http.set_host_interval(API_HOST, 0.25)

    def configuration_problem(self) -> str | None:
        if not self._token_source():
            return ("Apple Music is not configured: add a MusicKit developer token in Settings "
                    "to search the Apple Music catalog. Other providers are still used.")
        if not _STOREFRONT.match(self.storefront):
            return f"Apple Music storefront '{self.storefront}' is not a two-letter country code."
        return None

    def search(self, track: TrackQuery) -> list[ArtworkCandidate]:
        problem = self.configuration_problem()
        if problem:
            raise ProviderError(problem, ProviderStatus.NOT_CONFIGURED)
        headers = {"Authorization": f"Bearer {self._token_source()}", "Accept": "application/json"}
        base = f"{API_BASE}/{self.storefront}"
        isrc = (track.isrc or "").replace("-", "").strip().upper()
        try:
            songs: list[dict] = []
            if isrc:
                status, data = self.http.get_json(f"{base}/songs?filter%5Bisrc%5D={quote(isrc)}",
                                                  headers, accept_status=(200, 401, 403, 404))
                self._check_status(status)
                songs = (data or {}).get("data", []) if isinstance(data, dict) else []
            if not songs and track.artist and track.title:
                parsed = parse_title(track.title)
                term = " ".join(x for x in (track.artist, parsed.base or track.title, parsed.raw_version) if x)
                url = f"{base}/search?term={quote(term)}&types=songs&limit=15"
                status, data = self.http.get_json(url, headers, accept_status=(200, 401, 403, 404))
                self._check_status(status)
                results = (data or {}).get("results", {}) if isinstance(data, dict) else {}
                songs = (results.get("songs") or {}).get("data", [])
        except RateLimitedError as exc:
            raise ProviderError(str(exc), ProviderStatus.RATE_LIMITED) from exc
        except NetworkError as exc:
            raise ProviderError(str(exc), ProviderStatus.OFFLINE) from exc
        except HttpError as exc:
            raise ProviderError(f"Apple Music request failed: {exc}") from exc
        return [c for c in (self._candidate(s, isrc) for s in songs) if c]

    def _check_status(self, status: int) -> None:
        if status in (401, 403):
            raise ProviderError("Apple Music rejected the developer token (it may have expired).",
                                ProviderStatus.NOT_CONFIGURED)
        if status == 404:
            raise ProviderError(
                f"Apple Music catalog search is not available for storefront '{self.storefront}' "
                "in the current configuration.", ProviderStatus.UNAVAILABLE)

    def _candidate(self, song: dict, isrc: str) -> ArtworkCandidate | None:
        attrs = song.get("attributes") or {}
        art = attrs.get("artwork") or {}
        template = art.get("url")
        if not template:
            return None
        w = int(art.get("width") or MAX_EDGE)
        h = int(art.get("height") or MAX_EDGE)
        scale = min(1.0, MAX_EDGE / max(w, h))
        w, h = max(1, int(w * scale)), max(1, int(h * scale))
        song_isrc = (attrs.get("isrc") or "").upper()
        return ArtworkCandidate(
            provider=self.name,
            image_url=artwork_url(template, w, h),
            thumbnail_url=artwork_url(template, 300, 300),
            width=w, height=h,
            release_id=song.get("id"),
            release_title=attrs.get("albumName", "") or "",
            release_artist=attrs.get("artistName", "") or "",
            recording_title=attrs.get("name", "") or "",
            release_date=attrs.get("releaseDate", "") or "",
            label=attrs.get("recordLabel", "") or "",
            isrc=song_isrc,
            source_url=attrs.get("url"),
            attribution=ATTRIBUTION,
            license=LICENSE_NOTE,
            identifier_match="isrc" if isrc and song_isrc == isrc else "",
        )
