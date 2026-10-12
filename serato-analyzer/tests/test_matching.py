from serato_analyzer.artwork.matching import (
    likely_unofficial, parse_title, rank_candidates, score_candidate, web_search_terms,
)
from serato_analyzer.artwork.models import ArtworkCandidate, Confidence, TrackQuery


def cand(artist, title, album="", date="", isrc="", ident="", url=None, width=None):
    return ArtworkCandidate(provider="musicbrainz", image_url=url or f"https://x/{artist}/{title}/{album}",
                            release_artist=artist, recording_title=title, release_title=album or title,
                            release_date=date, isrc=isrc, identifier_match=ident, width=width)


def test_exact_match_is_high():
    t = TrackQuery(artist="Disclosure", title="Latch", album="Settle", year="2013")
    r = score_candidate(t, cand("Disclosure", "Latch", "Settle", "2013-05-31"))
    assert r.confidence == Confidence.HIGH and r.score > 0.95


def test_missing_album_metadata_still_matches():
    t = TrackQuery(artist="Disclosure", title="Latch")
    assert score_candidate(t, cand("Disclosure", "Latch", "Settle")).confidence == Confidence.HIGH


def test_artist_alone_never_matches():
    t = TrackQuery(artist="Disclosure", title="Latch", album="Settle")
    r = score_candidate(t, cand("Disclosure", "White Noise", "Settle"))
    assert r.confidence == Confidence.LOW
    assert any("Title does not match" in x for x in r.reasons)


def test_title_alone_never_matches():
    t = TrackQuery(artist="Disclosure", title="Latch")
    assert score_candidate(t, cand("Some Covers Band", "Latch")).confidence == Confidence.LOW


def test_remix_vs_original_is_capped():
    t = TrackQuery(artist="Sam Smith", title="Latch (Disclosure Remix)")
    original = score_candidate(t, cand("Sam Smith", "Latch"))
    assert original.confidence != Confidence.HIGH
    remix = score_candidate(t, cand("Sam Smith", "Latch (Disclosure Remix)"))
    assert remix.confidence == Confidence.HIGH
    assert remix.score > original.score


def test_different_remixes_differ():
    t = TrackQuery(artist="Calvin Harris", title="Feel So Close (Nero Remix)")
    r = score_candidate(t, cand("Calvin Harris", "Feel So Close (Benny Benassi Remix)"))
    assert r.confidence != Confidence.HIGH


def test_dj_pool_tags_are_neutral():
    t = TrackQuery(artist="Drake", title="Hotline Bling (Clean)")
    assert score_candidate(t, cand("Drake", "Hotline Bling", "Views")).confidence == Confidence.HIGH
    t2 = TrackQuery(artist="Drake", title="Hotline Bling (Dirty Intro)")
    assert score_candidate(t2, cand("Drake", "Hotline Bling", "Views")).confidence == Confidence.HIGH


def test_dj_edit_is_never_high_without_identifier():
    t = TrackQuery(artist="Daft Punk", title="Get Lucky (Ben Stevens Edit)")
    assert likely_unofficial(t)
    r = score_candidate(t, cand("Daft Punk", "Get Lucky (Ben Stevens Edit)"))
    assert r.confidence == Confidence.MEDIUM
    assert any("DJ edit" in x for x in r.reasons)


def test_bootleg_detected():
    assert likely_unofficial(TrackQuery(artist="X", title="Song (Y Bootleg)"))
    assert not likely_unofficial(TrackQuery(artist="X", title="Song (Radio Edit)"))


def test_isrc_match_boosts():
    t = TrackQuery(artist="Disclosure", title="Latch", isrc="GB-UM7-12-05432")
    r = score_candidate(t, cand("Disclosure feat. Sam Smith", "Latch", isrc="GBUM71205432"))
    assert r.confidence == Confidence.HIGH and "ISRC matches" in r.reasons[0]


def test_release_id_from_tags_is_high():
    t = TrackQuery(artist="Disclosure", title="Latch", mb_release_id="abc")
    r = score_candidate(t, cand("Disclosure", "Latch", ident="mb_release_id"))
    assert r.confidence == Confidence.HIGH


def test_featured_artists_and_accents():
    t = TrackQuery(artist="Beyoncé feat. Jay-Z", title="Crazy in Love")
    assert score_candidate(t, cand("Beyonce & JAY Z", "Crazy In Love")).confidence == Confidence.HIGH


def test_year_mismatch_reason():
    t = TrackQuery(artist="A", title="B", year="1999")
    r = score_candidate(t, cand("A", "B", date="2015-01-01"))
    assert any("year" in x.lower() for x in r.reasons)


def test_rank_multiple_candidates_and_dedupe():
    t = TrackQuery(artist="Disclosure", title="Latch", album="Settle")
    cands = [
        cand("Disclosure", "White Noise", url="u1"),
        cand("Disclosure", "Latch", "Latch (Remixes)", url="u2", width=500),
        cand("Disclosure", "Latch", "Settle", url="u3", width=1200),
        cand("Disclosure", "Latch", "Settle", url="u3"),  # duplicate image
    ]
    ranked = rank_candidates(t, cands)
    assert [c.image_url for c in ranked] == ["u3", "u2", "u1"]
    assert ranked[-1].confidence == Confidence.LOW


def test_parse_title_variants():
    assert parse_title("One More Time - Radio Edit").version == "radio edit"
    assert parse_title("Song [feat. X] (VIP Mix)").version == "vip mix"
    assert parse_title("Love (Is All) (Extended Mix)").base == "love is all"


def test_web_search_terms():
    t = TrackQuery(artist="Disclosure", title="Latch (Disclosure Remix)", album="Settle", year="2013")
    q = web_search_terms(t)
    assert q == "Disclosure Latch (Disclosure Remix) Settle 2013 cover art"
