"""HTTP client behaviour and provider parsing, against mocked responses only."""

import pytest

from conftest import (
    REL_REMIX, REL_SETTLE, REL_SINGLE, FakeTransport, OfflineTransport, install_musicbrainz,
    make_image, mb_recording, mb_release,
)
from serato_analyzer.artwork.http import (
    HttpClient, HttpError, NetworkError, RateLimitedError, RateLimiter, Response, ResponseTooLarge,
    UnsafeUrlError,
)
from serato_analyzer.artwork.models import Confidence, TrackQuery
from serato_analyzer.artwork.providers import (
    AppleMusicProvider, MusicBrainzProvider, ProviderError, ProviderStatus,
)
from serato_analyzer.artwork.providers.manual import candidate_from_url, web_search_url


def client(transport, clock):
    return HttpClient(transport, clock=clock.time, sleep=clock.sleep)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def test_rate_limiter_spaces_requests(fake_clock):
    lim = RateLimiter(1.0, fake_clock.time, fake_clock.sleep)
    for _ in range(3):
        lim.wait()
    assert fake_clock.sleeps == [1.0, 1.0]


def test_musicbrainz_requests_are_rate_limited(transport, fake_clock):
    http = client(transport, fake_clock)
    MusicBrainzProvider(http)
    transport.add_json("musicbrainz.org", {"recordings": []})
    for _ in range(3):
        http.get("https://musicbrainz.org/ws/2/recording?query=x")
    assert fake_clock.sleeps and all(s >= 1.0 for s in fake_clock.sleeps)
    assert len(fake_clock.sleeps) == 2


def test_retry_after_on_503_is_honoured(transport, fake_clock):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return Response(503, b"", url, {"Retry-After": "5"})
        return Response(200, b'{"ok": true}', url)

    transport.add("example.org", flaky)
    status, data = client(transport, fake_clock).get_json("https://example.org/x")
    assert status == 200 and data == {"ok": True}
    assert 5.0 in fake_clock.sleeps


def test_persistent_rate_limit_raises(transport, fake_clock):
    transport.add("example.org", Response(429, b"", "u", {"Retry-After": "1"}))
    with pytest.raises(RateLimitedError):
        client(transport, fake_clock).get("https://example.org/x")


def test_huge_retry_after_gives_up_quickly(transport, fake_clock):
    transport.add("example.org", Response(503, b"", "u", {"Retry-After": "3600"}))
    with pytest.raises(RateLimitedError):
        client(transport, fake_clock).get("https://example.org/x")
    assert sum(fake_clock.sleeps) < 60


def test_network_errors_retry_then_raise(fake_clock):
    t = OfflineTransport()
    with pytest.raises(NetworkError):
        client(t, fake_clock).get("https://example.org/x")
    assert len(t.calls) == 3


def test_server_error_raises(transport, fake_clock):
    transport.add("example.org", Response(500, b"boom", "u"))
    with pytest.raises(HttpError) as exc:
        client(transport, fake_clock).get("https://example.org/x")
    assert exc.value.status == 500


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y.jpg", "javascript:alert(1)", "//x/y"])
def test_only_http_urls(url, transport, fake_clock):
    with pytest.raises(UnsafeUrlError):
        client(transport, fake_clock).get(url)


def test_download_size_cap(transport, fake_clock):
    transport.add_bytes("big.example", b"x" * 5000)
    with pytest.raises(ResponseTooLarge):
        client(transport, fake_clock).get("https://big.example/a.jpg", max_bytes=1000)


def test_invalid_json_is_an_error(transport, fake_clock):
    transport.add("example.org", Response(200, b"<html>", "u"))
    with pytest.raises(HttpError):
        client(transport, fake_clock).get_json("https://example.org/x")


def test_user_agent_is_sent(fake_clock):
    seen = {}

    class T:
        def get(self, url, headers, timeout, max_bytes):
            seen.update(headers)
            return Response(200, b"{}", url)

    client(T(), fake_clock).get("https://musicbrainz.org/ws/2/x")
    assert seen["User-Agent"].startswith("SeratoAnalyzer/")


# ---------------------------------------------------------------------------
# MusicBrainz / Cover Art Archive
# ---------------------------------------------------------------------------

def test_musicbrainz_search_returns_cover_candidates(transport, fake_clock):
    install_musicbrainz(transport, [
        mb_recording("Latch", "Disclosure", [mb_release(REL_SETTLE, "Settle"),
                                             mb_release(REL_SINGLE, "Latch", rtype="Single")]),
    ], covers={REL_SETTLE: make_image(), REL_SINGLE: make_image()})
    prov = MusicBrainzProvider(client(transport, fake_clock))
    cands = prov.search(TrackQuery(artist="Disclosure", title="Latch", album="Settle"))
    assert {c.release_id for c in cands} == {REL_SETTLE, REL_SINGLE}
    c = next(c for c in cands if c.release_id == REL_SETTLE)
    assert c.image_url.startswith("https://") and c.image_url.endswith("front-1200.jpg")
    assert c.thumbnail_url.endswith("front-250.jpg")
    assert c.source_url == f"https://musicbrainz.org/release/{REL_SETTLE}"
    assert "Cover Art Archive" in c.attribution and c.license


def test_musicbrainz_release_without_art(transport, fake_clock):
    install_musicbrainz(transport, [mb_recording("Latch", "Disclosure", [mb_release(REL_SETTLE, "Settle")])])
    prov = MusicBrainzProvider(client(transport, fake_clock))
    assert prov.search(TrackQuery(artist="Disclosure", title="Latch")) == []


def test_musicbrainz_uses_release_id_tag_directly(transport, fake_clock):
    install_musicbrainz(transport, [], covers={REL_SETTLE: make_image()})
    prov = MusicBrainzProvider(client(transport, fake_clock))
    cands = prov.search(TrackQuery(artist="Disclosure", title="Latch", mb_release_id=REL_SETTLE))
    assert cands and cands[0].identifier_match == "mb_release_id"


def test_musicbrainz_isrc_lookup(transport, fake_clock):
    install_musicbrainz(transport, [], covers={REL_SINGLE: make_image()},
                        isrc_recordings=[mb_recording("Latch", "Disclosure", [mb_release(REL_SINGLE, "Latch")])])
    prov = MusicBrainzProvider(client(transport, fake_clock))
    cands = prov.search(TrackQuery(artist="Disclosure", title="Latch", isrc="GBUM71205432"))
    assert cands[0].isrc == "GBUM71205432" and cands[0].identifier_match == "isrc"
    assert any("/isrc/GBUM71205432" in u for u in transport.calls)


def test_musicbrainz_remix_falls_back_to_base_title(transport, fake_clock):
    def search(url):
        import json
        if "Remix" in url:
            return Response(200, json.dumps({"recordings": []}).encode(), url)
        return Response(200, json.dumps({"recordings": [mb_recording(
            "Latch", "Sam Smith", [mb_release(REL_REMIX, "Latch (Remixes)")], disambiguation="Disclosure remix")]}).encode(), url)

    transport.add("musicbrainz.org/ws/2/recording", search)
    install_musicbrainz(transport, None or [], covers={REL_REMIX: make_image()})
    transport.add("musicbrainz.org/ws/2/recording", search)
    prov = MusicBrainzProvider(client(transport, fake_clock))
    cands = prov.search(TrackQuery(artist="Sam Smith", title="Latch (Disclosure Remix)"))
    assert cands and cands[0].recording_title == "Latch (Disclosure remix)"


def test_musicbrainz_failures_map_to_status(transport, fake_clock):
    transport.add("musicbrainz.org", Response(500, b"", "u"))
    prov = MusicBrainzProvider(client(transport, fake_clock))
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.ERROR

    prov = MusicBrainzProvider(client(OfflineTransport(), fake_clock))
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.OFFLINE

    t = FakeTransport()
    t.add("musicbrainz.org", Response(503, b"", "u", {"Retry-After": "1"}))
    prov = MusicBrainzProvider(client(t, fake_clock))
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.RATE_LIMITED


# ---------------------------------------------------------------------------
# Apple Music
# ---------------------------------------------------------------------------

APPLE_SONG = {"id": "1440", "attributes": {
    "name": "Latch", "artistName": "Disclosure", "albumName": "Settle", "releaseDate": "2013-05-31",
    "isrc": "GBUM71205432", "url": "https://music.apple.com/us/album/latch/1",
    "artwork": {"url": "https://is1-ssl.mzstatic.com/image/thumb/x/{w}x{h}bb.jpg", "width": 3000, "height": 3000}}}


def test_apple_music_not_configured_is_reported(transport, fake_clock):
    prov = AppleMusicProvider(client(transport, fake_clock), lambda: "", "us")
    assert "not configured" in prov.configuration_problem()
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.NOT_CONFIGURED
    assert transport.calls == []


def test_apple_music_search(transport, fake_clock):
    transport.add_json("api.music.apple.com/v1/catalog/gb/search", {"results": {"songs": {"data": [APPLE_SONG]}}})
    prov = AppleMusicProvider(client(transport, fake_clock), lambda: "tok", "gb")
    cands = prov.search(TrackQuery(artist="Disclosure", title="Latch"))
    assert cands[0].image_url == "https://is1-ssl.mzstatic.com/image/thumb/x/3000x3000bb.jpg"
    assert cands[0].thumbnail_url.endswith("300x300bb.jpg")
    assert cands[0].width == 3000 and cands[0].attribution == "Apple Music"
    assert "/catalog/gb/" in transport.calls[0]


def test_apple_music_isrc_filter(transport, fake_clock):
    transport.add_json("songs?filter%5Bisrc%5D=GBUM71205432", {"data": [APPLE_SONG]})
    prov = AppleMusicProvider(client(transport, fake_clock), lambda: "tok", "us")
    cands = prov.search(TrackQuery(artist="Disclosure", title="Latch", isrc="GBUM71205432"))
    assert cands[0].identifier_match == "isrc"


def test_apple_music_bad_token_and_unavailable(transport, fake_clock):
    transport.add_json("api.music.apple.com", {"errors": []}, status=401)
    prov = AppleMusicProvider(client(transport, fake_clock), lambda: "bad", "us")
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.NOT_CONFIGURED

    t = FakeTransport()
    t.add_json("api.music.apple.com", {"errors": []}, status=404)
    prov = AppleMusicProvider(client(t, fake_clock), lambda: "tok", "zz")
    with pytest.raises(ProviderError) as exc:
        prov.search(TrackQuery(artist="A", title="B"))
    assert exc.value.status == ProviderStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Manual
# ---------------------------------------------------------------------------

def test_web_search_url_contains_metadata():
    url = web_search_url(TrackQuery(artist="Daft Punk", title="Get Lucky (Ben Stevens Edit)"), "duckduckgo")
    assert url.startswith("https://duckduckgo.com/") and "Daft+Punk" in url and "Get+Lucky" in url


def test_manual_url_candidate_is_low_confidence():
    c = candidate_from_url("https://example.com/cover.jpg")
    assert c.confidence == Confidence.LOW and "permission" in c.license
    with pytest.raises(UnsafeUrlError):
        candidate_from_url("file:///etc/passwd")
