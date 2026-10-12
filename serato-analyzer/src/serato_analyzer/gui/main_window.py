"""Main window: wires pages to the shared AppContext and the job queue."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox, QTabWidget

from .. import APP_DISPLAY_NAME
from ..context import AppContext
from ..jobs import Job, JobEvent
from ..reports import artwork_report, export_report
from ..scanner import ScanErrors, iter_audio_files
from .artwork_finder import ArtworkFinderPage
from .common import JobBridge
from .pages import DashboardPage, LibraryPage, QueuePage, ReportsPage, SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1360, 900)
        self.bridge = JobBridge(self)
        self.bridge.event.connect(self._on_job_event)
        ctx.jobs.add_listener(self.bridge)

        self.tabs = QTabWidget()
        self.dashboard = DashboardPage(self)
        self.library = LibraryPage(self)
        self.queue = QueuePage(self)
        self.artwork = ArtworkFinderPage(self)
        self.reports = ReportsPage(self)
        self.settings_page = SettingsPage(self)
        self.pages = {"dashboard": self.dashboard, "library": self.library, "queue": self.queue,
                      "artwork": self.artwork, "reports": self.reports, "settings": self.settings_page}
        for key, title in (("dashboard", "Dashboard"), ("library", "Folders && Crates"),
                           ("queue", "Analysis Queue"), ("artwork", "Artwork Finder"),
                           ("reports", "Reports"), ("settings", "Settings")):
            self.tabs.addTab(self.pages[key], title)
        self.tabs.currentChanged.connect(lambda _i: self._on_tab())
        self.setCentralWidget(self.tabs)
        self.jobs_label = QLabel("")
        self.statusBar().addPermanentWidget(self.jobs_label)

    # Navigation ----------------------------------------------------------
    def show_page(self, key: str) -> None:
        self.tabs.setCurrentWidget(self.pages[key])

    def _on_tab(self) -> None:
        page = self.tabs.currentWidget()
        if page in (self.dashboard, self.reports):
            page.refresh()

    def refresh_all(self) -> None:
        self.dashboard.refresh()
        self.reports.refresh()
        self.artwork.model.refresh_all()

    # Jobs ----------------------------------------------------------------
    def run_job(self, job: Job) -> Job:
        return self.ctx.jobs.submit(job)

    def _on_job_event(self, ev: JobEvent) -> None:
        self.queue.on_job_event(ev)
        self.artwork.on_job_event(ev)
        active = self.ctx.jobs.active()
        self.jobs_label.setText(f"{len(active)} job(s) running" if active else "")
        job = ev.job
        if not ev.finished:
            return
        if job.kind == "scan":
            ids = [r.value for r in job.results if r.ok and isinstance(r.value, int)]
            self.library.show_tracks(ids, job.scope)
            denied = getattr(job, "scan_errors", None)
            if denied and denied.permission_denied:
                QMessageBox.warning(self, "Some folders could not be read",
                                    "\n\n".join(h for _p, h in denied.permission_denied[:5]))
            if getattr(job, "then_find_artwork", False):
                self.open_in_finder(ids, job.scope, search=True)
            self.dashboard.refresh()
        elif job.kind == "artwork_select":
            self.artwork.after_select(job)
        elif job.kind == "artwork_preview":
            self.artwork.after_preview(job)
        elif job.kind == "artwork_apply":
            self.artwork.after_apply(job)
            self.dashboard.refresh()
        elif job.kind == "artwork_search":
            self.dashboard.refresh()

    # Workflows used by several pages ---------------------------------------
    def choose_and_scan(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if folder:
            self.scan_folder(Path(folder))

    def scan_folder(self, folder: Path, then_find_artwork: bool = False) -> None:
        check = self.ctx.platform.check_access(folder)
        if not check.ok:
            QMessageBox.warning(self, "Cannot read folder", check.hint)
            return
        errors = ScanErrors()
        files = list(iter_audio_files(folder, errors=errors, hint=self.ctx.platform.permission_hint))
        self.scan_paths(files, folder, f"Folder: {folder}", then_find_artwork, errors)

    def scan_paths(self, files: list[Path], scan_root: Path | None, scope: str,
                   then_find_artwork: bool = False, errors: ScanErrors | None = None) -> None:
        scanner = self.ctx.scanner
        job = Job(f"Scan {scope} ({len(files)} files)", files,
                  lambda p, j: scanner.scan_file(p, scan_root), kind="scan", describe=lambda p: p.name)
        job.scope = scope
        job.then_find_artwork = then_find_artwork
        job.scan_errors = errors
        self.run_job(job)
        self.show_page("library" if not then_find_artwork else "queue")

    def open_in_finder(self, track_ids: list[int], scope: str, search: bool = False) -> None:
        self.artwork.load_tracks(track_ids, scope)
        self.show_page("artwork")
        if search:
            todo = self.ctx.artwork.tracks_needing_artwork(track_ids)
            if todo and not self.ctx.settings.offline_mode:
                self.artwork.start_search(todo, interactive=False)

    def export_report(self, track_ids: list[int] | None) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export artwork report", "artwork-report.csv",
                                              "CSV (*.csv);;JSON (*.json)")
        if path:
            export_report(artwork_report(self.ctx.db, track_ids), Path(path))
            self.statusBar().showMessage(f"Report saved to {path}", 8000)

    def closeEvent(self, event) -> None:
        if self.ctx.jobs.active():
            if QMessageBox.question(self, "Jobs running", "Background jobs are still running. Quit anyway?") \
                    != QMessageBox.Yes:
                event.ignore()
                return
        self.artwork.model.thumbs.shutdown()
        self.artwork.card_thumbs.shutdown()
        super().closeEvent(event)
