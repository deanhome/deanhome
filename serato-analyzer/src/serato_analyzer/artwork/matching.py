"""Query construction and candidate ranking.

The ranking never trusts an artist name alone: a candidate needs both the
artist *and* the title (or an exact identifier such as a MusicBrainz release
ID) to reach medium or high confidence.  Remix/version mismatches and tracks
that look like unofficial DJ edits are capped so a human always reviews them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .models import ArtworkCandidate, Confidence, TrackQuery

HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.65
MIN_COMPONENT_FOR_MATCH = 0.6

WEIGHTS = {
    "artist": 0.35,
    "title": 0.35,
    "version": 0.10,
    "album": 0.08,
    "year": 0.05,
    "label": 0.07,
}

# Version annotations that do not change which release (and so which cover) a
# track belongs to.  Many are DJ-pool annotations.
NEUTRAL_VERSIONS = {
    "", "original", "original mix", "original version", "extended", "extended mix",
    "extended version", "radio edit", "radio", "radio version", "single version", "single edit",
    "album version", "album edit", "main", "main mix", "clean", "dirty", "explicit", "intro",
    "outro", "intro clean", "intro dirty", "clean intro", "dirty intro", "intro edit clean",
    "quick hit", "short edit", "remastered", "remaster", "mono", "stereo",
}

_NEUTRAL_WORDS = {
    "clean", "dirty", "explicit", "intro", "outro", "extended", "radio", "original", "mix",
    "version", "short", "quick", "hit", "main", "album", "single", "remastered", "remaster",
    "mono", "stereo",
}

UNOFFICIAL_MARKERS = re.compile(
    r"\b(bootleg|boot|mash ?up|mash-up|rework|re-work|flip|unofficial|redrum|re-?rub|blend|"
    r"dj edit|transition|remake|refix|re-?edit|vs\.?)\b",
    re.IGNORECASE,
)

_VERSION_KEYWORDS = re.compile(
    r"\b(mix|remix|edit|version|rework|bootleg|dub|vip|instrumental|acapella|a cappella|extended|"
    r"radio|club|original|intro|outro|clean|dirty|explicit|remaster(ed)?|live|flip|mash ?up|blend|"
    r"redrum|re-?rub|refix|quick hit|mono|stereo)\b",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\s+[^\(\)\[\]]*[\)\]]?", re.IGNORECASE)
_POOL_TAGS = re.compile(r"\b(clean|dirty|explicit)\b")
_BRACKETED = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+(.+)$")
_ARTIST_SPLIT = re.compile(r"\s*(?:,|;|/|&|\+|\band\b|\bx\b|\bvs\.?\b|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b)\s*",
                           re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Case-fold, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("&", " and ").replace("$", "s")
    text = _NON_WORD.sub(" ", text).replace("_", " ")
    return _SPACES.sub(" ", text).strip()


@dataclass
class ParsedTitle:
    base: str
    version: str           # normalised version text, "" when none
    raw_version: str = ""  # as written, for display / web searches

    @property
    def is_neutral_version(self) -> bool:
        return self.version in NEUTRAL_VERSIONS


def parse_title(title: str) -> ParsedTitle:
    """Split "Song (Artist Remix) [feat. X]" into base title and version."""
    if not title:
        return ParsedTitle("", "")
    work = _FEAT.sub(" ", title)
    versions: list[str] = []

    def take(match: re.Match) -> str:
        inner = match.group(1).strip()
        if _VERSION_KEYWORDS.search(inner):
            versions.append(inner)
            return " "
        return match.group(0)

    work = _BRACKETED.sub(take, work)
    dash = _DASH_SUFFIX.search(work)
    if dash and _VERSION_KEYWORDS.search(dash.group(1)):
        versions.append(dash.group(1).strip())
        work = work[: dash.start()]
    raw = " ".join(versions)
    # Clean/dirty/explicit are DJ-pool annotations, not different releases.
    version = _SPACES.sub(" ", _POOL_TAGS.sub(" ", normalize(raw))).strip()
    # Combined DJ-pool tags such as "Clean Extended" are neutral when every word is.
    if version and version not in NEUTRAL_VERSIONS and all(w in _NEUTRAL_WORDS for w in version.split()):
        version = "original"
    return ParsedTitle(normalize(work), version, raw)


def split_artists(artist: str) -> list[str]:
    parts = [normalize(p) for p in _ARTIST_SPLIT.split(artist or "")]
    return [p for p in parts if p]


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    direct = SequenceMatcher(None, a, b).ratio()
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    sorted_ratio = SequenceMatcher(None, sa, sb).ratio()
    return max(direct, sorted_ratio)


def artist_similarity(track_artist: str, candidate_artist: str) -> float:
    full = similarity(track_artist, candidate_artist)
    t_parts, c_parts = split_artists(track_artist), split_artists(candidate_artist)
    if not t_parts or not c_parts:
        return full
    # The primary (first-listed) artist must be present on the candidate.
    primary = max(similarity(t_parts[0], c) for c in c_parts)
    others = [max(similarity(t, c) for c in c_parts) for t in t_parts[1:]]
    combined = primary if not others else 0.75 * primary + 0.25 * (sum(others) / len(others))
    return max(full, combined)


def likely_unofficial(track: TrackQuery) -> bool:
    """Heuristic: a DJ edit / bootleg / mashup that may have no public cover."""
    haystack = " ".join([track.title, track.album])
    if UNOFFICIAL_MARKERS.search(haystack):
        return True
    version = parse_title(track.title).version
    words = version.split()
    return "edit" in words and version not in NEUTRAL_VERSIONS


def _year(text: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m.group(0)) if m else None


@dataclass
class MatchResult:
    score: float
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)


def score_candidate(track: TrackQuery, cand: ArtworkCandidate) -> MatchResult:
    reasons: list[str] = []
    comps: dict[str, float] = {}

    t_title = parse_title(track.title)
    c_title_text = cand.recording_title or cand.release_title
    c_title = parse_title(c_title_text)

    if track.artist and cand.release_artist:
        comps["artist"] = artist_similarity(track.artist, cand.release_artist)
    if t_title.base and c_title.base:
        comps["title"] = similarity(t_title.base, c_title.base)
        # A single titled after the track counts as a title match too.
        if cand.recording_title and cand.release_title:
            comps["title"] = max(comps["title"], similarity(t_title.base, parse_title(cand.release_title).base) * 0.95)

    # Version / remix
    t_neutral, c_neutral = t_title.is_neutral_version, c_title.is_neutral_version
    version_mismatch = False
    if t_neutral and c_neutral:
        comps["version"] = 1.0
    elif not t_neutral and not c_neutral:
        comps["version"] = similarity(t_title.version, c_title.version)
        if comps["version"] < 0.6:
            version_mismatch = True
            reasons.append(f"Different version: track is '{t_title.raw_version}', "
                           f"candidate is '{c_title.raw_version}'")
    else:
        comps["version"] = 0.2
        version_mismatch = True
        if not t_neutral:
            reasons.append(f"Track is a version ('{t_title.raw_version}') but candidate looks like "
                           "the original release")
        else:
            reasons.append(f"Candidate is a different version ('{c_title.raw_version}')")

    if track.album and cand.release_title:
        comps["album"] = similarity(parse_title(track.album).base or track.album,
                                    parse_title(cand.release_title).base or cand.release_title)

    ty, cy = _year(track.year), _year(cand.release_date)
    if ty and cy:
        diff = abs(ty - cy)
        comps["year"] = 1.0 if diff == 0 else 0.6 if diff == 1 else 0.0
        if diff > 1:
            reasons.append(f"Release year {cy} differs from tagged year {ty}")

    label_scores = []
    if track.label and cand.label:
        label_scores.append(similarity(track.label, cand.label))
    if track.catalog_number and cand.catalog_number:
        same = normalize(track.catalog_number).replace(" ", "") == normalize(cand.catalog_number).replace(" ", "")
        label_scores.append(1.0 if same else 0.0)
        if same:
            reasons.append("Catalog number matches")
    if label_scores:
        comps["label"] = max(label_scores)

    total_w = sum(WEIGHTS[k] for k in comps)
    score = sum(WEIGHTS[k] * v for k, v in comps.items()) / total_w if total_w else 0.0

    artist_s = comps.get("artist", 0.0)
    title_s = comps.get("title", 0.0)
    for name, value in (("Artist", artist_s), ("Title", title_s)):
        if value >= 0.95:
            reasons.insert(0, f"{name} matches")
        elif value >= MIN_COMPONENT_FOR_MATCH:
            reasons.insert(0, f"{name} similar ({value:.0%})")
        else:
            reasons.insert(0, f"{name} does not match ({value:.0%})")
    if "album" in comps and comps["album"] >= 0.9:
        reasons.append("Album matches")

    # Exact identifiers
    id_match = cand.identifier_match
    if id_match == "mb_release_id" and track.mb_release_id:
        score = max(score, 0.97)
        reasons.insert(0, "MusicBrainz release ID from the file's tags")
    elif track.isrc and cand.isrc and track.isrc.replace("-", "").upper() == cand.isrc.replace("-", "").upper():
        id_match = "isrc"
        if artist_s >= 0.5:
            score = max(score, 0.9)
        reasons.insert(0, "ISRC matches")

    # Confidence + caps
    if score >= HIGH_THRESHOLD:
        confidence = Confidence.HIGH
    elif score >= MEDIUM_THRESHOLD:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW

    has_exact_id = id_match in ("mb_release_id", "isrc")
    if not has_exact_id and (artist_s < MIN_COMPONENT_FOR_MATCH or title_s < MIN_COMPONENT_FOR_MATCH):
        confidence = Confidence.LOW  # never match on artist (or title) alone
    if version_mismatch and confidence == Confidence.HIGH and id_match != "mb_release_id":
        confidence = Confidence.MEDIUM
    if likely_unofficial(track) and not has_exact_id and confidence == Confidence.HIGH:
        confidence = Confidence.MEDIUM
        reasons.append("Track looks like a DJ edit or unofficial version; it may have no official cover")

    return MatchResult(round(score, 4), confidence, reasons, comps)


def rank_candidates(track: TrackQuery, candidates: list[ArtworkCandidate]) -> list[ArtworkCandidate]:
    """Score every candidate in place and return them best-first, de-duplicated."""
    seen: set[str] = set()
    ranked: list[ArtworkCandidate] = []
    for cand in candidates:
        if cand.image_url in seen:
            continue
        seen.add(cand.image_url)
        result = score_candidate(track, cand)
        cand.score, cand.confidence, cand.reasons = result.score, result.confidence, result.reasons
        ranked.append(cand)
    ranked.sort(key=lambda c: (c.confidence.rank, c.score, (c.width or 0)), reverse=True)
    return ranked


def web_search_terms(track: TrackQuery) -> str:
    parts = [track.artist, track.title]
    if track.album and normalize(track.album) != normalize(track.title):
        parts.append(track.album)
    if track.catalog_number:
        parts.append(track.catalog_number)
    elif track.year:
        parts.append(_year(track.year) and str(_year(track.year)) or "")
    parts.append("cover art")
    return " ".join(p.strip() for p in parts if p and p.strip())
