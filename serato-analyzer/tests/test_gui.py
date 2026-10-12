"""Offscreen smoke tests for the Qt interface (skipped when Qt cannot load)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from conftest import REL_SETTLE, install_musicbrainz, make_image, make_mp3, mb_recording, mb_release  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def pump(qapp, until, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_main_window_scan_search_and_review(qapp, ctx, music, transport):
    from serato_analyzer.gui.main_window import MainWindow

    install_musicbrainz(transport, [mb_recording("Latch", "Disclosure", [mb_release(REL_SETTLE, "Settle")])],
                        covers={REL_SETTLE: make_image(1200, 1200)})
    make_mp3(music / "latch.mp3", {"artist": "Disclosure", "title": "Latch", "album": "Settle"})
    make_mp3(music / "done.mp3", {"artist": "X", "title": "Y"}, artwork=make_image(1000, 1000))

    win = MainWindow(ctx)
    win.show()
    for key in win.pages:
        win.show_page(key)
        qapp.processEvents()

    win.scan_folder(music, then_find_artwork=True)
    page = win.artwork
    assert pump(qapp, lambda: page.search_job is not None and page.search_job.is_finished)
    assert page.model.rowCount() == 2
    tid = ctx.db.get_track_by_path(str(music / "latch.mp3"))["id"]
    row = page.model.track_ids.index(tid)
    # Job events reach the GUI thread through queued signals; wait for the table to update.
    assert pump(qapp, lambda: page.model.data(page.model.index(row, 5)) == "Candidates found")
    assert page.model.data(page.model.index(row, 7)) == "High"

    page.view.selectRow(row)
    qapp.processEvents()
    assert len(page.cards) == 1

    cand = ctx.artwork.candidates(tid)[0]
    page._select(tid, cand)
    assert pump(qapp, lambda: ctx.db.get_artwork_state(tid)["review_status"] == "approved")

    from serato_analyzer.gui.artwork_finder import ChangesDialog

    dlg = ChangesDialog(win, [tid])
    assert dlg.table.rowCount() == 1 and dlg.apply_btn.isEnabled()
    assert "Serato_Analyzed_Music" not in dlg.table.item(0, 5).text()
    dlg.close()

    win.dashboard.refresh()
    assert win.dashboard.stat_labels["tracks"].text() == "2"
    win.reports.refresh()
    win.queue.refresh()
    assert win.queue.table.rowCount() >= 3
    # Closing with jobs still running asks for confirmation (a modal dialog), so let them finish.
    assert pump(qapp, lambda: not ctx.jobs.active())
    win.close()
