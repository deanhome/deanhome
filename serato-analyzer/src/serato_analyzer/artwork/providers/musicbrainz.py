"""MusicBrainz search + Cover Art Archive retrieval.

* MusicBrainz web service: https://musicbrainz.org/doc/MusicBrainz_API
  - Requires a meaningful User-Agent and at most ~1 request per second.
* Cover Art Archive: https://musicbrainz.org/doc/Cover_Art_Archive/API
  - Images belong to their rights holders; we show that with each candidate.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..http import HttpClient, HttpError, NetworkError, RateLimitedError
from ..matching import parse_title, score_candidate
from ..models import ArtworkCandidate, TrackQuery
from .base import ArtworkProvider, ProviderError, ProviderStatus

MB_HOST = "musicbrainz.org"
MB_BASE = f"https://{MB_HOST}/ws/2"
CAA_BASE = "https://coverartarchive.org"
MB_INTERVAL = 1.1      # seconds between MusicBrainz requests
CAA_INTERVAL = 0.5

ATTRIBUTION = "Cover Art Archive (MusicBrainz)"
LICENSE_NOTE = ("Cover image copyright belongs to its rights holder; supplied via the Cover Art "
                "Archive. Use only as permitted for your personal library.")

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")


def lucene_escape(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def _artist_credit(credit: list | None) -> str:
    if not credit:
        return ""
    out = []
    for part in credit:
        if isinstance(part, dict):
            out.append(part.get("name") or (part.get("artist") or {}).get("name", ""))
            out.append(part.get("joinphrase", ""))
    return "".join(out).strip()


class MusicBrainzProvider(ArtworkProvider):
    name = "musicbrainz"
    display_name = "MusicBrainz / Cover Art Archive"

    def __init__(self, http: HttpClient, max_releases: int = 5) -> None:
        self.http = http
        self.max_releases = max_releases
        http.set_host_interval(MB_HOST, MB_INTERVAL)
        http.set_host_interval("coverartarchive.org", CAA_INTERVAL)

    # ------------------------------------------------------------------
    def search(self, track: TrackQuery) -> list[ArtworkCandidate]:
        try:
            releases = self._find_releases(track)
            candidates: list[ArtworkCandidate] = []
            for rel in releases[: self.max_releases]:
                candidates.extend(self._cover_art(rel))
            return candidates
        except RateLimitedError as exc:
            raise ProviderError(str(exc), ProviderStatus.RATE_LIMITED) from exc
        except NetworkError as exc:
            raise ProviderError(str(exc), ProviderStatus.OFFLINE) from exc
        except HttpError as exc:
            raise ProviderError(f"MusicBrainz request failed: {exc}") from exc

    # ------------------------------------------------------------------
    def _find_releases(self, track: TrackQuery) -> list[dict]:
        """Return release dicts (best first) with the metadata we score on."""
        releases: list[dict] = []
        mbid = (track.mb_release_id or "").strip().lower()
        if _MBID.match(mbid):
            releases.append({"id": mbid, "title": track.album, "artist": track.artist,
                             "recording_title": track.title, "date": track.year,
                             "identifier_match": "mb_release_id"})
        isrc = (track.isrc or "").replace("-", "").strip().upper()
        recordings: list[dict] = []
        if _ISRC.match(isrc):
            status, data = self.http.get_json(
                f"{MB_BASE}/isrc/{isrc}?inc=releases+artist-credits+release-groups&fmt=json",
                accept_status=(200, 404))
            if status == 200 and isinstance(data, dict):
                for rec in data.get("recordings", []):
                    # Every recording returned by an ISRC lookup carries that ISRC.
                    rec["isrcs"] = [isrc, *[i for i in (rec.get("isrcs") or []) if i != isrc]]
                    recordings.append(rec)
        if not recordings and track.artist and track.title:
            recordings = self._search_recordings(track)

        for rec in recordings:
            rec_artist = _artist_credit(rec.get("artist-credit"))
            rec_title = rec.get("title", "")
            if rec.get("disambiguation"):
                rec_title = f"{rec_title} ({rec['disambiguation']})"
            rec_isrcs = rec.get("isrcs") or []
            for rel in rec.get("releases", []) or []:
                rg = rel.get("release-group") or {}
                label_info = (rel.get("label-info") or [{}])[0] if rel.get("label-info") else {}
                releases.append({
                    "id": rel.get("id"),
                    "title": rel.get("title", ""),
                    "artist": _artist_credit(rel.get("artist-credit")) or rec_artist,
                    "recording_title": rec_title,
                    "date": rel.get("date", ""),
                    "type": rg.get("primary-type", "") or "",
                    "status": rel.get("status", ""),
                    "label": ((label_info.get("label") or {}).get("name", "")) if label_info else "",
                    "catalog_number": label_info.get("catalog-number", "") if label_info else "",
                    "isrc": isrc if isrc in rec_isrcs else (rec_isrcs[0] if rec_isrcs else ""),
                    "identifier_match": "isrc" if isrc and isrc in rec_isrcs else "",
                })

        # Pre-rank releases with the same scorer so we only ask the Cover Art
        # Archive about the plausible ones; prefer official releases.
        seen: set[str] = set()
        unique = []
        for rel in releases:
            if rel.get("id") and _MBID.match(rel["id"]) and rel["id"] not in seen:
                seen.add(rel["id"])
                unique.append(rel)

        def rank(rel: dict) -> tuple:
            probe = self._candidate_shell(rel, "", None)
            result = score_candidate(track, probe)
            official = rel.get("status", "Official") in ("Official", "")
            return (rel.get("identifier_match") == "mb_release_id", result.confidence.rank,
                    official, result.score)

        unique.sort(key=rank, reverse=True)
        return unique

    def _search_recordings(self, track: TrackQuery) -> list[dict]:
        parsed = parse_title(track.title)
        # Search for the full title (with remix info) first, then the base title.
        # The album is deliberately not part of the query: DJ libraries often
        # have no or a wrong album tag, and the ranking already scores it.
        query = (f'recording:"{lucene_escape(track.title)}" AND '
                 f'artist:"{lucene_escape(track.artist)}"')
        url = f"{MB_BASE}/recording?query={quote(query)}&limit=10&fmt=json"
        _status, data = self.http.get_json(url)
        recordings = (data or {}).get("recordings", []) if isinstance(data, dict) else []
        if not recordings and parsed.raw_version:
            base_title = re.sub(r"[\(\[].*?[\)\]]", "", track.title).split(" - ")[0].strip()
            query = (f'recording:"{lucene_escape(base_title)}" AND '
                     f'artist:"{lucene_escape(track.artist)}"')
            _status, data = self.http.get_json(f"{MB_BASE}/recording?query={quote(query)}&limit=10&fmt=json")
            recordings = (data or {}).get("recordings", []) if isinstance(data, dict) else []
        return recordings

    def _candidate_shell(self, rel: dict, image_url: str, thumb: str | None) -> ArtworkCandidate:
        return ArtworkCandidate(
            provider=self.name,
            image_url=image_url,
            thumbnail_url=thumb,
            release_id=rel.get("id"),
            release_title=rel.get("title", "") or "",
            release_artist=rel.get("artist", "") or "",
            recording_title=rel.get("recording_title", "") or "",
            release_date=rel.get("date", "") or "",
            release_type=rel.get("type", "") or "",
            label=rel.get("label", "") or "",
            catalog_number=rel.get("catalog_number", "") or "",
            isrc=rel.get("isrc", "") or "",
            source_url=f"https://musicbrainz.org/release/{rel.get('id')}",
            attribution=ATTRIBUTION,
            license=LICENSE_NOTE,
            identifier_match=rel.get("identifier_match", ""),
        )

    def _cover_art(self, rel: dict) -> list[ArtworkCandidate]:
        status, data = self.http.get_json(f"{CAA_BASE}/release/{rel['id']}", accept_status=(200, 404))
        if status != 200 or not isinstance(data, dict):
            return []
        out = []
        images = data.get("images", []) or []
        fronts = [im for im in images if im.get("front") or "Front" in (im.get("types") or [])]
        for im in fronts or images[:1]:
            thumbs = im.get("thumbnails") or {}
            full = im.get("image")
            if not full:
                continue
            # The 1200px rendition is plenty for a 1000px target and much smaller to fetch.
            best = thumbs.get("1200") or full
            thumb = thumbs.get("250") or thumbs.get("small") or thumbs.get("500") or best
            cand = self._candidate_shell(rel, _https(best), _https(thumb))
            cand.full_image_url = _https(full)
            if im.get("comment"):
                cand.reasons.append(f"Cover Art Archive note: {im['comment']}")
            out.append(cand)
        return out


def _https(url: str | None) -> str | None:
    if url and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url
