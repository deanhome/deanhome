"""The three application modes, the default-safe behaviour, and reporting."""

import json
from pathlib import Path

import pytest

from conftest import REL_SETTLE, install_musicbrainz, make_image, make_mp3, make_wav, mb_recording, mb_release
from serato_analyzer.artwork.embed import audio_fingerprint
from serato_analyzer.artwork.models import ApplyMode
from serato_analyzer.fsutil import sha256_file
from serato_analyzer.metadata import read_metadata
from serato_analyzer.reports import artwork_report, export_report
from serato_analyzer.scanner import iter_audio_files


def approve(ctx, transport, music, name="House/latch.mp3", artwork=None, maker=make_mp3):
    install_musicbrainz(transport, [mb_recording("Latch", "Disclosure", [mb_release(REL_SETTLE, "Settle")])],
                        covers={REL_SETTLE: make_image(1200, 1200)})
    path = music / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if maker is make_mp3:
        maker(path, {"artist": "Disclosure", "title": "Latch", "album": "Settle"}, artwork=artwork)
    else:
        maker(path, {"artist": "Disclosure", "title": "Latch"})
    for p in iter_audio_files(music):
        ctx.scanner.scan_file(p, music)
    tid = ctx.db.get_track_by_path(str(path))["id"]
    best = ctx.artwork.search_track(tid).candidates[0]
    assert ctx.artwork.select_candidate(tid, best.db_id).ok
    return tid, path


def test_mode1_saves_image_only(ctx, music, transport):
    tid, src = approve(ctx, transport, music)
    before = sha256_file(src)
    [out] = ctx.applier.apply([tid], ApplyMode.SAVE_SEPARATELY)
    assert out.ok and out.destination.is_file()
    assert sha256_file(src) == before
    assert not list(src.parent.glob("*.jpg"))
    assert not out.destination.is_relative_to(Path(ctx.platform.cache_dir()))  # survives cache clears


def test_mode1_alongside_music_optional(ctx, music, transport):
    tid, src = approve(ctx, transport, music)
    ctx.settings.save_artwork_alongside_music = True
    [out] = ctx.applier.apply([tid], ApplyMode.SAVE_SEPARATELY)
    assert (src.parent / "latch.jpg").is_file()


def test_mode3_default_copies_and_embeds(ctx, music, transport, tmp_path):
    tid, src = approve(ctx, transport, music)
    before = sha256_file(src)
    plan = ctx.applier.plan([tid], ApplyMode.PREPARE_FOR_SERATO)[0]
    assert not plan.blocked and not plan.modifies_original
    assert plan.destination == tmp_path / "out" / "House" / "latch.mp3"

    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert out.ok, out.message
    assert sha256_file(src) == before, "originals are never modified by default"
    md = read_metadata(out.destination)
    assert md.artwork.present and md.artwork.width == 1000 and md.title == "Latch"
    assert audio_fingerprint(out.destination) == audio_fingerprint(src)
    state = ctx.db.get_artwork_state(tid)
    assert state["review_status"] == "applied" and state["embedding_status"] == "verified"


def test_mode3_unsupported_format_copies_with_sidecar(ctx, music, transport):
    tid, src = approve(ctx, transport, music, name="t.wav", maker=make_wav)
    # Pretend the copy is a format that cannot hold art, by renaming the source.
    weird = src.with_suffix(".aac")
    src.rename(weird)
    ctx.db.execute("UPDATE tracks SET path = ? WHERE id = ?", (str(weird), tid))
    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert out.ok and out.status == "copied"
    assert out.destination.with_suffix(".jpg").is_file()
    assert ctx.db.get_artwork_state(tid)["embedding_status"] == "unsupported_format"


def test_mode2_copy_and_in_place_gates(ctx, music, transport):
    tid, src = approve(ctx, transport, music)
    plan = ctx.applier.plan([tid], ApplyMode.EMBED, in_place=True)[0]
    assert plan.blocked and "turned off" in plan.blocked_reason

    ctx.settings.allow_modify_originals = True
    with pytest.raises(PermissionError):
        ctx.applier.apply([tid], ApplyMode.EMBED, in_place=True)
    original = src.read_bytes()
    [out] = ctx.applier.apply([tid], ApplyMode.EMBED, in_place=True, confirmed=True)
    assert out.ok and out.backup_path and Path(out.backup_path).read_bytes() == original
    assert read_metadata(src).artwork.present
    # Recovery
    backup = ctx.backups.list(tid)[0]
    ctx.backups.restore(backup["id"])
    assert src.read_bytes() == original


def test_existing_artwork_requires_explicit_replacement(ctx, music, transport):
    old = make_image(300, 300, color=(0, 0, 200))
    tid, src = approve(ctx, transport, music, artwork=old)
    plan = ctx.applier.plan([tid], ApplyMode.PREPARE_FOR_SERATO)[0]
    assert plan.blocked and "already has embedded artwork" in plan.blocked_reason
    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert not out.ok and out.status == "skipped"

    ctx.artwork.request_replacement(tid)
    plan = ctx.applier.plan([tid], ApplyMode.PREPARE_FOR_SERATO)[0]
    assert plan.replaces_existing_artwork and any("Replaces existing" in w for w in plan.warnings)
    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert out.ok and read_metadata(out.destination).artwork.width == 1000
    assert read_metadata(src).artwork.data == old  # original untouched


def test_unapproved_tracks_are_not_applied(ctx, music):
    make_mp3(music / "a.mp3", {"artist": "A", "title": "T"})
    tid = ctx.scanner.scan_file(music / "a.mp3", music)
    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert not out.ok and "No approved artwork" in out.message


def test_tampered_cache_image_refused(ctx, music, transport):
    tid, _src = approve(ctx, transport, music)
    img = Path(ctx.db.get_artwork_state(tid)["selected_image_path"])
    img.write_bytes(make_image(1000, 1000, color=(1, 1, 1)))
    [out] = ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    assert not out.ok and "changed since it was approved" in out.message


def test_destination_name_collisions(ctx, music, transport, tmp_path):
    tid, _ = approve(ctx, transport, music)
    clash = tmp_path / "out" / "House" / "latch.mp3"
    clash.parent.mkdir(parents=True)
    clash.write_bytes(b"someone else's file")
    plan = ctx.applier.plan([tid], ApplyMode.PREPARE_FOR_SERATO)[0]
    assert plan.destination.name == "latch (2).mp3"


def test_report_export(ctx, music, transport, tmp_path):
    tid, _ = approve(ctx, transport, music)
    make_mp3(music / "skip.mp3", {"artist": "B", "title": "Skip"})
    skip_id = ctx.scanner.scan_file(music / "skip.mp3", music)
    ctx.artwork.skip(skip_id)
    ctx.applier.apply([tid], ApplyMode.PREPARE_FOR_SERATO)
    report = artwork_report(ctx.db)
    assert report["summary"]["completed"] == 1 and report["summary"]["skipped"] == 1
    csv_path = export_report(report, tmp_path / "r.csv")
    text = csv_path.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("outcome,artist") and "completed" in text
    data = json.loads(export_report(report, tmp_path / "r.json").read_text())
    assert data["tracks"][0]["provider"] in ("musicbrainz", "")
