"""Manual artwork: browser search, pasted image URLs, and local image files.

Search engines are *not* used as APIs: we only build a search URL and open it
in the user's browser.  The user then pastes the URL of an image they are
allowed to use, or imports a file.  Nothing is scraped.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from ..http import check_url
from ..matching import web_search_terms
from ..models import ArtworkCandidate, Confidence, TrackQuery

SEARCH_ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q={q}&iax=images&ia=images",
    "google": "https://www.google.com/search?tbm=isch&q={q}",
    "bing": "https://www.bing.com/images/search?q={q}",
}

MANUAL_URL = "manual_url"
MANUAL_FILE = "manual_file"
MANUAL_NOTE = "Supplied by you. Make sure you have permission to use this image."


def web_search_url(track: TrackQuery, engine: str = "duckduckgo") -> str:
    template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["duckduckgo"])
    return template.format(q=quote_plus(web_search_terms(track)))


def candidate_from_url(url: str) -> ArtworkCandidate:
    url = check_url(url)
    return ArtworkCandidate(provider=MANUAL_URL, image_url=url, thumbnail_url=url, source_url=url,
                            attribution="Image URL you supplied", license=MANUAL_NOTE,
                            confidence=Confidence.LOW, reasons=["Chosen manually"])


def candidate_from_file(path: Path) -> ArtworkCandidate:
    path = Path(path)
    uri = path.resolve().as_uri()
    return ArtworkCandidate(provider=MANUAL_FILE, image_url=uri, thumbnail_url=None, source_url=uri,
                            attribution=f"Local file {path.name}", license=MANUAL_NOTE,
                            confidence=Confidence.LOW, reasons=["Chosen manually"])
