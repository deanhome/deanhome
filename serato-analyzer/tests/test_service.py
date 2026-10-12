"""Scan -> search -> review -> select, with mocked providers."""

from pathlib import Path

import pytest

from conftest import (
    REL_SETTLE, REL_SINGLE, OfflineTransport, install_musicbrainz, make_image, make_mp3, mb_recording,
    mb_release,
)
from serato_analyzer.artwork.http import HttpClient, Response
from serato_analyzer.artwork.images import InvalidImageError
from serato_analyzer.artwork.models import Confidence, ReviewStatus, SearchStatus
from serato_analyzer.artwork.service import DownloadError
from serato_analyzer.scanner import iter_audio_files


def scan(ctx, folder):
    return [ctx.scanner.scan_file(p, folder) for p in iter_audio_files(folder)]


def latch_routes(transport, cover=None):
    install_musicbrainz(transport, [
        mb_recording("Latch", "Disclosure", [mb_release(REL_SETTLE, "Settle"),
                                             mb_release(REL_SINGLE, "Latch", rtype="Single")]),
    ], covers={REL_SETTLE: cover or make_image(1400, 1400), REL_SINGLE: make_image(600, 600)})


def test_scan_detects_artwork_states(ctx, music):
    make_mp3(music / "missing.mp3", {"artist": "A", "title": "Missing"})
    make_mp3(music / "small.mp3", {"artist": "A", "title": "Small"}, artwork=make_image(200, 200))
    make_mp3(music / "wide.mp3", {"artist": "A", "title": "Wide"}, artwork=make_image(1600, 900))
    make_mp3(music / "good.mp3", {"artist": "A", "title": "Good"}, artwork=make_image(1000, 1000))
    (music / "._good.mp3").write_bytes(b"appledouble junk")
    ids = scan(ctx, music)
    states = {r["title"]: r["artwork_status"] for r in ctx.db.artwork_rows(ids)}
    assert states == {"Missing": "missing", "Small": "low_resolution", "Wide": "non_square", "Good": "ok"}
    need = {ctx.db.get_track(i)["title"] for i in ctx.artwork.tracks_needing_artwork(ids)}
    assert need == {"Missing", "Small", "Wide"}
    good = ctx.db.get_track_by_path(str(music / "good.mp3"))["id"]
    ctx.artwork.request_replacement(good)
    assert good in ctx.artwork.tracks_needing_artwork(ids)


def test_rescan_skips_unchanged_files(ctx, music, monkeypatch):
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    scan(ctx, music)
    import serato_analyzer.scanner as sc
    monkeypatch.setattr(sc, "read_metadata", lambda p: pytest.fail("should not re-read"))
    scan(ctx, music)


def test_unreadable_file_is_recorded_not_fatal(ctx, music):
    (music / "bad.mp3").write_bytes(b"nope")
    make_mp3(music / "ok.mp3", {"artist": "A", "title": "T"})
    ids = scan(ctx, music)
    rows = {Path(r["path"]).name: r for r in ctx.db.artwork_rows(ids)}
    assert rows["bad.mp3"]["read_error"] and rows["ok.mp3"]["artwork_status"] == "missing"


def test_search_ranks_and_stores_candidates(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "latch.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    [tid] = scan(ctx, music)
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.FOUND
    assert s.candidates[0].release_id == REL_SETTLE and s.candidates[0].confidence == Confidence.HIGH
    stored = ctx.artwork.candidates(tid)
    assert [c.release_id for c in stored][:1] == [REL_SETTLE] and len(stored) == 2
    state = ctx.db.get_artwork_state(tid)
    assert state["search_status"] == "found" and "musicbrainz" in state["providers_queried"]
    # Nothing is selected or applied automatically.
    assert state["review_status"] == "pending" and state["selected_image_path"] is None


def test_repeat_searches_use_cache(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "latch.mp3", {"artist": "Disclosure", "title": "Latch"})
    [tid] = scan(ctx, music)
    ctx.artwork.search_track(tid)
    n = len(transport.calls)
    assert ctx.artwork.search_track(tid).from_cache
    assert len(transport.calls) == n
    # "Search again" bypasses the per-track result but the provider cache still avoids traffic
    # for an identical query, unless forced.
    ctx.artwork.search_track(tid, force=True)
    assert len(transport.calls) > n


def test_same_query_from_two_tracks_hits_provider_cache(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch"})
    make_mp3(music / "b.mp3", {"artist": "Disclosure", "title": "Latch"})
    a, b = scan(ctx, music)
    ctx.artwork.search_track(a)
    n = len(transport.calls)
    assert ctx.artwork.search_track(b).status == SearchStatus.FOUND
    assert len(transport.calls) == n


def test_no_candidates_for_dj_edit_suggests_manual(ctx, music, transport):
    install_musicbrainz(transport, [])
    make_mp3(music / "edit.mp3", {"artist": "Daft Punk", "title": "Get Lucky (Ben Stevens Edit)"})
    [tid] = scan(ctx, music)
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.NO_CANDIDATES
    assert "DJ edit" in s.message and "manually" in s.message


def test_missing_metadata_cannot_search(ctx, music, transport):
    make_mp3(music / "untagged.mp3")
    [tid] = scan(ctx, music)
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.NO_CANDIDATES and "Search Web" in s.message
    assert transport.calls == []


def test_offline_mode_makes_no_requests(ctx, music, transport):
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    [tid] = scan(ctx, music)
    ctx.settings.offline_mode = True
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.OFFLINE and transport.calls == []


def test_network_down_reports_offline(ctx, music, fake_clock):
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    [tid] = scan(ctx, music)
    ctx.artwork.http = HttpClient(OfflineTransport(), clock=fake_clock.time, sleep=fake_clock.sleep)
    ctx.artwork.apply_settings(ctx.settings)
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.OFFLINE
    assert ctx.db.get_artwork_state(tid)["last_error"]


def test_api_failure_reports_failed(ctx, music, transport):
    transport.add("musicbrainz.org", Response(500, b"", "u"))
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    [tid] = scan(ctx, music)
    assert ctx.artwork.search_track(tid).status == SearchStatus.FAILED


def test_unconfigured_apple_music_is_reported_but_search_continues(ctx, music, transport):
    latch_routes(transport)
    ctx.settings.provider_apple_music = True
    ctx.artwork.apply_settings(ctx.settings)
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch"})
    [tid] = scan(ctx, music)
    s = ctx.artwork.search_track(tid)
    assert s.status == SearchStatus.FOUND
    assert "Apple Music is not configured" in s.message
    assert not any("apple.com" in u for u in transport.calls)


def test_select_downloads_validates_and_normalises(ctx, music, transport):
    latch_routes(transport, cover=make_image(1400, 1400))
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    [tid] = scan(ctx, music)
    best = ctx.artwork.search_track(tid).candidates[0]
    res = ctx.artwork.select_candidate(tid, best.db_id)
    assert res.ok and (res.width, res.height) == (1000, 1000)
    state = ctx.db.get_artwork_state(tid)
    assert state["review_status"] == "approved" and state["download_status"] == "downloaded"
    assert Path(state["selected_image_path"]).is_file()
    assert Path(state["selected_image_path"]).is_relative_to(Path(ctx.platform.artwork_cache_dir()))
    assert not list(music.glob("*.jpg"))  # nothing written next to the music
    assert ctx.artwork.get_candidate(best.db_id).width == 1400


def test_invalid_download_rejected(ctx, music, transport):
    latch_routes(transport)
    transport.add_bytes(f"coverartarchive.org/release/{REL_SETTLE}/front-1200.jpg", b"<html>blocked</html>")
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    [tid] = scan(ctx, music)
    best = ctx.artwork.search_track(tid).candidates[0]
    with pytest.raises(DownloadError):
        ctx.artwork.select_candidate(tid, best.db_id)
    state = ctx.db.get_artwork_state(tid)
    assert state["download_status"] == "failed" and state["review_status"] == "pending"


def test_unapproved_host_refused(ctx):
    assert not ctx.artwork.url_allowed("musicbrainz", "https://evil.example/cover.jpg")
    assert ctx.artwork.url_allowed("musicbrainz", "https://coverartarchive.org/x.jpg")
    assert ctx.artwork.url_allowed("apple_music", "https://is1-ssl.mzstatic.com/x.jpg")
    assert not ctx.artwork.url_allowed("apple_music", "https://mzstatic.com.evil.example/x.jpg")
    with pytest.raises(DownloadError, match="unapproved"):
        ctx.artwork.download_image("musicbrainz", "https://evil.example/cover.jpg")


def test_non_square_selection_asks_for_crop(ctx, music, transport):
    latch_routes(transport, cover=make_image(1600, 900))
    ctx.settings.artwork_crop_mode = "ask"
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    [tid] = scan(ctx, music)
    best = ctx.artwork.search_track(tid).candidates[0]
    res = ctx.artwork.select_candidate(tid, best.db_id)
    assert res.needs_crop and not res.ok
    assert ctx.db.get_artwork_state(tid)["review_status"] == "pending"
    res = ctx.artwork.set_crop(tid, crop_box=(350, 0, 900))
    assert res.ok and res.width == 900


def test_manual_url_and_file(ctx, music, transport, tmp_path):
    transport.add_bytes("images.example.com/cover.png", make_image(800, 800, "PNG"))
    make_mp3(music / "edit.mp3", {"artist": "X", "title": "Y (Z Bootleg)"})
    [tid] = scan(ctx, music)
    cand = ctx.artwork.add_manual_url(tid, "https://images.example.com/cover.png")
    assert ctx.artwork.select_candidate(tid, cand.db_id).ok

    img = tmp_path / "my cover.jpg"
    img.write_bytes(make_image(700, 700))
    fcand = ctx.artwork.add_manual_file(tid, img)
    assert ctx.artwork.select_candidate(tid, fcand.db_id).width == 700

    bad = tmp_path / "fake.jpg"
    bad.write_bytes(b"MZ\x90\x00 not an image")
    with pytest.raises(InvalidImageError):
        ctx.artwork.add_manual_file(tid, bad)


def test_review_actions(ctx, music):
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    [tid] = scan(ctx, music)
    ctx.artwork.skip(tid)
    assert ctx.db.get_artwork_state(tid)["review_status"] == ReviewStatus.SKIPPED.value
    ctx.artwork.mark_no_artwork(tid)
    assert tid not in ctx.artwork.tracks_needing_artwork([tid])
    ctx.artwork.reset_review(tid)
    assert tid in ctx.artwork.tracks_needing_artwork([tid])


def test_auto_select_off_by_default_and_never_replaces(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    make_mp3(music / "b.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"},
             artwork=make_image(200, 200))
    a, b = scan(ctx, music)
    ctx.artwork.search_track(a)
    assert ctx.db.get_artwork_state(a)["review_status"] == "pending"

    ctx.settings.auto_select_high_confidence = True
    ctx.artwork.search_track(a, force=True)
    assert ctx.db.get_artwork_state(a)["review_status"] == "approved"
    ctx.artwork.search_track(b, force=True)
    assert ctx.db.get_artwork_state(b)["review_status"] == "pending"


def test_rejected_candidate_hidden(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch"})
    [tid] = scan(ctx, music)
    first = ctx.artwork.search_track(tid).candidates[0]
    ctx.artwork.reject_candidate(first.db_id)
    assert first.db_id not in [c.db_id for c in ctx.artwork.candidates(tid)]


def test_clear_cache_keeps_music(ctx, music, transport):
    latch_routes(transport)
    src = make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"},
                   artwork=None)
    before = src.read_bytes()
    [tid] = scan(ctx, music)
    best = ctx.artwork.search_track(tid).candidates[0]
    ctx.artwork.select_candidate(tid, best.db_id)
    ctx.artwork.clear_cache()
    assert src.read_bytes() == before
    assert ctx.db.query_one("SELECT COUNT(*) FROM provider_cache")[0] == 0
    assert ctx.db.query_one("SELECT COUNT(*) FROM image_cache")[0] == 0
    assert ctx.db.get_artwork_state(tid)["review_status"] == "pending"


def test_prune_cache(ctx, music, transport):
    latch_routes(transport)
    make_mp3(music / "a.mp3", {"artist": "Disclosure", "title": "Latch"})
    [tid] = scan(ctx, music)
    ctx.artwork.search_track(tid)
    ctx.db.execute("UPDATE provider_cache SET fetched_at = 0")
    assert ctx.artwork.prune_cache()["searches"] >= 1
