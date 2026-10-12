"""Dashboard, Library (folder scanner + crate browser), Queue, Reports and Settings pages."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..artwork.apply import SERATO_IMPORT_INSTRUCTIONS
from ..config import Settings
from ..jobs import Job, JobEvent
from ..platforms import SeratoLibrary
from ..reports import artwork_report
from ..serato.crates import Crate, crate_track_files, list_crates
from .common import label

if TYPE_CHECKING:
    from .main_window import MainWindow


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    return t


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.stats = QGridLayout()
        self.stat_labels: dict[str, QLabel] = {}
        for i, (key, text) in enumerate((
                ("tracks", "Tracks scanned"), ("missing_artwork", "Missing artwork"),
                ("low_quality_artwork", "Low-quality artwork"), ("awaiting_review", "Awaiting review"),
                ("approved", "Approved"), ("applied", "Applied"), ("failed", "Failed"))):
            box = QGroupBox(text)
            v = QVBoxLayout(box)
            num = QLabel("0")
            num.setStyleSheet("font-size: 26px; font-weight: 600;")
            v.addWidget(num)
            self.stat_labels[key] = num
            self.stats.addWidget(box, i // 4, i % 4)
        lay.addLayout(self.stats)

        actions = QHBoxLayout()
        for text, slot in (("Scan a music folder…", win.choose_and_scan),
                           ("Find missing artwork in library", self._find_all),
                           ("Open Artwork Finder", lambda: win.show_page("artwork"))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)

        lower = QHBoxLayout()
        self.locations = QListWidget()
        self.locations.itemDoubleClicked.connect(self._scan_location)
        g1 = QGroupBox("Music locations and Serato libraries (double-click to scan)")
        QVBoxLayout(g1).addWidget(self.locations)
        lower.addWidget(g1, 1)
        self.health = QPlainTextEdit()
        self.health.setReadOnly(True)
        g2 = QGroupBox("System and providers")
        QVBoxLayout(g2).addWidget(self.health)
        lower.addWidget(g2, 1)
        lay.addLayout(lower, 1)
        self.refresh()

    def refresh(self) -> None:
        ctx = self.win.ctx
        for key, value in ctx.db.counts().items():
            if key in self.stat_labels:
                self.stat_labels[key].setText(str(value))
        self.locations.clear()
        pf = ctx.platform
        for root in pf.candidate_music_roots():
            it = QListWidgetItem(f"📁 {root}")
            it.setData(Qt.UserRole, str(root))
            self.locations.addItem(it)
        for lib in pf.discover_serato_libraries():
            it = QListWidgetItem(f"🎛 Serato library: {lib.root}")
            it.setData(Qt.UserRole, str(lib.volume_root if not lib.is_system else pf.default_music_dir()))
            self.locations.addItem(it)
        lines = [f"Platform: {pf.name} ({pf.machine_arch()})"]
        for d in pf.detect_dependencies():
            state = "ok" if d.available else ("MISSING" if d.required else "not found")
            lines.append(f"{d.name}: {state} {d.version or ''} {d.detail}".rstrip())
        lines.append("")
        lines.append("Offline mode: ON" if ctx.settings.offline_mode else "Online artwork search: on")
        for name, problem in ctx.artwork.provider_overview():
            lines.append(f"{name}: {problem or 'ready'}")
        lines.append(f"Output folder: {ctx.applier.output_root()}")
        self.health.setPlainText("\n".join(lines))

    def _scan_location(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.UserRole))
        check = self.win.ctx.platform.check_access(path)
        if not check.ok:
            QMessageBox.warning(self, "Cannot read folder", check.hint)
            return
        self.win.scan_folder(path)

    def _find_all(self) -> None:
        ids = self.win.ctx.artwork.tracks_needing_artwork()
        self.win.open_in_finder(ids, "Library tracks needing artwork", search=True)


# ---------------------------------------------------------------------------
# Library: folder scanner + crate browser
# ---------------------------------------------------------------------------

class LibraryPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.current_ids: list[int] = []
        self.current_scope = ""
        split = QSplitter(Qt.Horizontal)
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        # Folder scanner
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("<b>Folder scanner</b>"))
        row = QHBoxLayout()
        choose = QPushButton("Choose folder and scan…")
        choose.clicked.connect(win.choose_and_scan)
        row.addWidget(choose)
        self.find_btn = QPushButton("Find artwork for these tracks")
        self.find_btn.clicked.connect(self._find_current)
        row.addWidget(self.find_btn)
        row.addStretch(1)
        lv.addLayout(row)
        self.scope = QLabel("")
        lv.addWidget(self.scope)
        self.tracks = _table(["Artist", "Title", "Album", "Format", "Artwork", "Path"])
        lv.addWidget(self.tracks, 1)
        split.addWidget(left)

        # Crate browser
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("<b>Serato crate browser</b>"))
        self.libraries = QComboBox()
        self.libraries.currentIndexChanged.connect(self._load_crates)
        rv.addWidget(self.libraries)
        refresh = QPushButton("Rescan drives for Serato libraries")
        refresh.clicked.connect(self.refresh_libraries)
        rv.addWidget(refresh)
        self.crates = QListWidget()
        self.crates.itemDoubleClicked.connect(lambda _i: self._scan_crate())
        rv.addWidget(self.crates, 1)
        scan_crate = QPushButton("Scan crate and find artwork")
        scan_crate.clicked.connect(self._scan_crate)
        rv.addWidget(scan_crate)
        split.addWidget(right)
        split.setSizes([700, 300])
        self.refresh_libraries()

    def show_tracks(self, ids: list[int], scope: str) -> None:
        self.current_ids, self.current_scope = ids, scope
        rows = self.win.ctx.db.artwork_rows(ids)
        self.scope.setText(f"{scope}: {len(rows)} track(s)")
        self.tracks.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [row["artist"] or "", row["title"] or "", row["album"] or "", row["format"] or "",
                      label(row["artwork_status"]) if not row["read_error"] else "Unreadable file",
                      row["path"]]
            for c, v in enumerate(values):
                it = QTableWidgetItem(v)
                if c == 5 and row["read_error"]:
                    it.setToolTip(row["read_error"])
                self.tracks.setItem(r, c, it)
        self.tracks.resizeColumnsToContents()

    def _find_current(self) -> None:
        if self.current_ids:
            self.win.open_in_finder(self.current_ids, self.current_scope, search=True)

    def refresh_libraries(self) -> None:
        self.libraries.blockSignals(True)
        self.libraries.clear()
        self.libs: list[SeratoLibrary] = self.win.ctx.platform.discover_serato_libraries()
        for lib in self.libs:
            self.libraries.addItem(f"{lib.label or 'Library'} — {lib.root}")
        if not self.libs:
            self.libraries.addItem("No Serato library found on connected drives")
        self.libraries.blockSignals(False)
        self._load_crates()

    def _load_crates(self) -> None:
        self.crates.clear()
        i = self.libraries.currentIndex()
        if not self.libs or i < 0 or i >= len(self.libs):
            return
        try:
            for crate in list_crates(self.libs[i]):
                it = QListWidgetItem(crate.name)
                it.setData(Qt.UserRole, crate)
                self.crates.addItem(it)
        except PermissionError:
            QMessageBox.warning(self, "Permission needed",
                                self.win.ctx.platform.permission_hint(self.libs[i].root))

    def _scan_crate(self) -> None:
        item = self.crates.currentItem()
        if not item:
            return
        crate: Crate = item.data(Qt.UserRole)
        lib = self.libs[self.libraries.currentIndex()]
        try:
            files = crate_track_files(self.win.ctx.platform, lib, crate)
        except Exception as exc:
            QMessageBox.warning(self, "Could not read crate", str(exc))
            return
        present = [f for f in files if f.is_file()]
        missing = len(files) - len(present)
        if missing:
            self.win.statusBar().showMessage(f"{missing} track(s) in this crate were not found on disk.", 10000)
        self.win.scan_paths(present, Path(lib.volume_root), f"Crate: {crate.name}", then_find_artwork=True)


# ---------------------------------------------------------------------------
# Analysis queue
# ---------------------------------------------------------------------------

class QueuePage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.table = _table(["Job", "State", "Progress", "Failures", "Current item"])
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in (("Pause", lambda j: j.pause()), ("Resume", lambda j: j.resume()),
                         ("Cancel", lambda j: j.cancel()),
                         ("Retry failed", lambda j: self.win.ctx.jobs.retry_failed(j))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: self._act(f))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

    def _selected_job(self) -> Job | None:
        r = self.table.currentRow()
        jobs = list(reversed(self.win.ctx.jobs.jobs))
        return jobs[r] if 0 <= r < len(jobs) else None

    def _act(self, fn) -> None:
        job = self._selected_job()
        if job:
            fn(job)
            self.refresh()

    def on_job_event(self, ev: JobEvent) -> None:
        self.refresh()

    def refresh(self) -> None:
        jobs = list(reversed(self.win.ctx.jobs.jobs))
        current = self.table.currentRow()
        self.table.setRowCount(len(jobs))
        for r, job in enumerate(jobs):
            values = [job.name, job.state.value, f"{job.done}/{job.total}", str(len(job.failed)), job.current]
            for c, v in enumerate(values):
                it = QTableWidgetItem(v)
                if c == 3 and job.failed:
                    it.setToolTip("\n".join(f.message for f in job.failed[:20]))
                self.table.setItem(r, c, it)
        if 0 <= current < len(jobs):
            self.table.selectRow(current)


# ---------------------------------------------------------------------------
# Reports and backups
# ---------------------------------------------------------------------------

class ReportsPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        exp = QPushButton("Export artwork report…")
        exp.clicked.connect(lambda: win.export_report(None))
        row.addWidget(exp)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("<b>Backups of modified files</b>"))
        self.backups = _table(["Created", "Original file", "Backup", "Restored"])
        lay.addWidget(self.backups, 1)
        rb = QPushButton("Restore selected backup…")
        rb.clicked.connect(self._restore)
        lay.addWidget(rb, alignment=Qt.AlignLeft)
        g = QGroupBox("Importing prepared tracks into Serato")
        v = QVBoxLayout(g)
        txt = QPlainTextEdit(SERATO_IMPORT_INSTRUCTIONS)
        txt.setReadOnly(True)
        v.addWidget(txt)
        open_out = QPushButton("Open Serato_Analyzed_Music folder")
        open_out.clicked.connect(self._open_output)
        v.addWidget(open_out, alignment=Qt.AlignLeft)
        lay.addWidget(g, 1)

    def refresh(self) -> None:
        s = artwork_report(self.win.ctx.db)["summary"]
        self.summary.setText(f"Completed: {s['completed']} · Skipped: {s['skipped']} · "
                             f"Failed: {s['failed']} · Pending: {s['pending']}")
        rows = self.win.ctx.backups.list()
        self.backup_rows = rows
        self.backups.setRowCount(len(rows))
        for r, b in enumerate(rows):
            for c, v in enumerate([b["created_at"], b["original_path"], b["backup_path"], b["restored_at"] or ""]):
                self.backups.setItem(r, c, QTableWidgetItem(v))

    def _restore(self) -> None:
        r = self.backups.currentRow()
        if r < 0:
            return
        b = self.backup_rows[r]
        if QMessageBox.question(self, "Restore backup",
                                f"Replace\n{b['original_path']}\nwith the backup made {b['created_at']}?") \
                != QMessageBox.Yes:
            return
        try:
            self.win.ctx.backups.restore(b["id"])
        except Exception as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))
            return
        if b["track_id"]:
            path = Path(b["original_path"])
            if path.is_file():
                self.win.ctx.scanner.scan_file(path, force=True)
        self.refresh()

    def _open_output(self) -> None:
        out = self.win.ctx.applier.output_root()
        out.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SETTING_LABELS = {
    "output_dir": "Output folder for prepared tracks (empty = Music/Serato_Analyzed_Music)",
    "offline_mode": "Offline mode (no online artwork search)",
    "provider_musicbrainz": "Use MusicBrainz / Cover Art Archive",
    "provider_apple_music": "Use Apple Music catalog (needs a developer token)",
    "apple_music_storefront": "Apple Music storefront (country code)",
    "apple_music_developer_token": "Apple Music developer token",
    "musicbrainz_contact": "Contact e-mail/URL sent to MusicBrainz (recommended)",
    "web_search_engine": "Search engine for 'Search Web for Cover Art'",
    "artwork_target_px": "Artwork size (square, pixels)",
    "artwork_min_acceptable_px": "Flag embedded artwork smaller than (pixels)",
    "artwork_crop_mode": "Non-square images",
    "artwork_max_download_bytes": "Maximum download size (bytes)",
    "artwork_max_dimension_px": "Maximum image dimension (pixels)",
    "search_cache_days": "Keep search results for (days)",
    "image_cache_days": "Keep cached images for (days)",
    "auto_select_high_confidence": "Automatically approve high-confidence matches (tracks without artwork only)",
    "allow_replace_existing_artwork": "Allow automatic approval to replace artwork marked for replacement",
    "allow_modify_originals": "Allow modifying original files (always backed up first)",
    "save_artwork_alongside_music": "When saving artwork separately, also save a .jpg beside the track",
}
CHOICES = {"artwork_crop_mode": ["ask", "center", "pad"], "web_search_engine": ["duckduckgo", "google", "bing"]}


class SettingsPage(QScrollArea):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        form = QFormLayout()
        self.editors: dict[str, QWidget] = {}
        s = win.ctx.settings
        for f in fields(Settings):
            if f.name not in SETTING_LABELS:
                continue
            value = getattr(s, f.name)
            if f.name in CHOICES:
                w = QComboBox()
                w.addItems(CHOICES[f.name])
                w.setCurrentText(value)
            elif isinstance(value, bool):
                w = QCheckBox()
                w.setChecked(value)
            elif isinstance(value, int):
                w = QSpinBox()
                w.setRange(0, 2_000_000_000)
                w.setValue(value)
            elif isinstance(value, float):
                w = QDoubleSpinBox()
                w.setValue(value)
            else:
                w = QLineEdit(str(value))
                if f.name == "apple_music_developer_token":
                    w.setEchoMode(QLineEdit.Password)
            self.editors[f.name] = w
            form.addRow(SETTING_LABELS[f.name], w)
        lay.addLayout(form)
        row = QHBoxLayout()
        save = QPushButton("Save settings")
        save.clicked.connect(self._save)
        row.addWidget(save)
        browse = QPushButton("Choose output folder…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        row.addStretch(1)
        lay.addLayout(row)
        cache = QGroupBox("Artwork cache")
        cv = QHBoxLayout(cache)
        for text, slot in (("Remove expired cache entries", self._prune),
                           ("Clear artwork cache", self._clear)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            cv.addWidget(b)
        cv.addWidget(QLabel("Clearing the cache never deletes music files or embedded artwork."))
        lay.addWidget(cache)
        lay.addStretch(1)
        self.setWidget(host)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.editors["output_dir"].setText(d)

    def _save(self) -> None:
        s = Settings(**{f.name: getattr(self.win.ctx.settings, f.name) for f in fields(Settings)})
        for name, w in self.editors.items():
            if isinstance(w, QComboBox):
                setattr(s, name, w.currentText())
            elif isinstance(w, QCheckBox):
                setattr(s, name, w.isChecked())
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                setattr(s, name, w.value())
            else:
                setattr(s, name, w.text().strip())
        if s.allow_modify_originals and not self.win.ctx.settings.allow_modify_originals:
            if QMessageBox.warning(self, "Allow modifying originals?",
                                   "Original audio files could then be changed (after a verified backup). "
                                   "You will still be asked to confirm each time. Enable?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                s.allow_modify_originals = False
                self.editors["allow_modify_originals"].setChecked(False)
        self.win.ctx.save_settings(s)
        self.win.statusBar().showMessage("Settings saved.", 5000)
        self.win.refresh_all()

    def _prune(self) -> None:
        r = self.win.ctx.artwork.prune_cache()
        QMessageBox.information(self, "Cache", f"Removed {r['searches']} search result(s) and {r['images']} image(s).")

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear artwork cache",
                                "Delete all cached search results and downloaded images? Approved selections "
                                "that have not been applied yet will need to be selected again. Your music "
                                "files and their embedded artwork are not affected.") == QMessageBox.Yes:
            self.win.ctx.artwork.clear_cache()
            self.win.refresh_all()
