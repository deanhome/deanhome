"""The Artwork Finder screen."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider,
    QSplitter, QTableView, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..artwork.apply import SERATO_IMPORT_INSTRUCTIONS
from ..artwork.models import ApplyMode, ArtworkCandidate
from ..jobs import Job, JobEvent, JobState
from ..metadata import read_embedded_artwork
from .common import ThumbnailLoader, confidence_color, label

if TYPE_CHECKING:
    from .main_window import MainWindow

THUMB = 56
COLUMNS = ["Artist", "Track title", "Album / release", "Current artwork", "Artwork status",
           "Search status", "Best candidate", "Confidence", "Source", "Review status"]
COL_CURRENT, COL_BEST = 3, 6


class ArtworkTableModel(QAbstractTableModel):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.track_ids: list[int] = []
        self.rows: dict[int, dict] = {}
        self.best: dict[int, ArtworkCandidate | None] = {}
        self.thumbs = ThumbnailLoader(self)
        self.thumbs.loaded.connect(self._thumb_loaded)
        self._thumb_rows: dict[str, tuple[int, int]] = {}

    # Data loading ----------------------------------------------------------
    def set_tracks(self, track_ids: list[int]) -> None:
        self.beginResetModel()
        self.track_ids = list(dict.fromkeys(track_ids))
        self._reload(self.track_ids)
        self.endResetModel()

    def _reload(self, ids: list[int]) -> None:
        ctx = self.win.ctx
        for row in ctx.db.artwork_rows(ids):
            self.rows[row["id"]] = dict(row)
            cands = ctx.artwork.candidates(row["id"])
            self.best[row["id"]] = cands[0] if cands else None

    def refresh(self, track_id: int) -> None:
        if track_id not in self.rows:
            return
        self._reload([track_id])
        self.thumbs.invalidate(f"cur:{track_id}")
        r = self.track_ids.index(track_id)
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def refresh_all(self) -> None:
        self.set_tracks(self.track_ids)

    def track_at(self, row: int) -> int:
        return self.track_ids[row]

    # Qt model --------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.track_ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        tid = self.track_ids[index.row()]
        row = self.rows.get(tid)
        if row is None:
            return None
        best = self.best.get(tid)
        col = index.column()
        if role == Qt.DisplayRole:
            return {
                0: row["artist"] or "", 1: row["title"] or Path(row["path"]).name, 2: row["album"] or "",
                3: "" if row["has_embedded"] else "None",
                4: label(row["artwork_status"]) + (" · replace" if row["replace_requested"] else ""),
                5: label(row["search_status"]),
                6: "" if best else "—",
                7: best.confidence.value.capitalize() if best else "",
                8: label(best.provider) if best else "",
                9: self._review_text(row),
            }.get(col)
        if role == Qt.DecorationRole:
            if col == COL_CURRENT and row["has_embedded"]:
                return self._thumb(f"cur:{tid}", index, lambda p=row["path"]: read_embedded_artwork(Path(p)).data)
            if col == COL_BEST and best:
                return self._thumb(f"cand:{best.thumbnail_url or best.image_url}", index,
                                   lambda b=best: self.win.ctx.artwork.fetch_thumbnail(b.provider, b.thumbnail_url or ""))
        if role == Qt.ForegroundRole and col == 7 and best:
            return confidence_color(best.confidence.value)
        if role == Qt.ToolTipRole:
            if col in (4, 5):
                return row["search_message"] or row["last_error"] or None
            if col == 0:
                return row["path"]
        if role == Qt.SizeHintRole and col in (COL_CURRENT, COL_BEST):
            return QSize(THUMB + 8, THUMB + 4)
        return None

    @staticmethod
    def _review_text(row: dict) -> str:
        # Tracks with good artwork that nobody asked to change need no review.
        if (row["review_status"] == "pending" and row["artwork_status"] == "ok"
                and not row["replace_requested"] and row["search_status"] == "not_searched"):
            return "—"
        return label(row["review_status"])

    def _thumb(self, key, index, fetch):
        self._thumb_rows[key] = (index.row(), index.column())
        return self.thumbs.get(key, fetch, THUMB)

    def _thumb_loaded(self, key: str, img) -> None:
        self.thumbs.store(key, img)
        pos = self._thumb_rows.get(key)
        if pos and pos[0] < len(self.track_ids):
            idx = self.index(*pos)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])


class CandidateCard(QFrame):
    selected = Signal(object)
    rejected = Signal(object)
    preview = Signal(object)

    def __init__(self, cand: ArtworkCandidate, thumbs: ThumbnailLoader, fetch) -> None:
        super().__init__()
        self.cand = cand
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedWidth(230)
        lay = QVBoxLayout(self)
        self.image = QLabel("Loading…")
        self.image.setFixedSize(200, 200)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setStyleSheet("background: palette(base); border: 1px solid palette(mid);")
        lay.addWidget(self.image, alignment=Qt.AlignHCenter)
        self._key = f"card:{cand.thumbnail_url or cand.image_url}"
        pix = thumbs.get(self._key, fetch, 200)
        if pix is not None:
            self.set_pixmap(pix)

        conf = QLabel(f"<b>{cand.confidence.value.capitalize()} confidence</b> ({cand.score:.0%})")
        conf.setStyleSheet(f"color: {confidence_color(cand.confidence.value).name()}")
        lay.addWidget(conf)
        release = " · ".join(x for x in (cand.release_title, cand.release_artist) if x)
        extra = " · ".join(x for x in (cand.release_date[:10], cand.release_type, cand.label,
                                       cand.catalog_number) if x)
        info = QLabel(f"{release}<br><small>{extra}</small><br><small>{cand.dimensions_text} · "
                      f"{label(cand.provider)}</small>")
        info.setWordWrap(True)
        lay.addWidget(info)
        attribution = QLabel(f"<small><i>{cand.attribution}</i></small>")
        attribution.setWordWrap(True)
        attribution.setToolTip(cand.license)
        lay.addWidget(attribution)
        if cand.reasons:
            self.setToolTip("\n".join(cand.reasons))

        buttons = QHBoxLayout()
        sel = QPushButton("Select")
        sel.clicked.connect(lambda: self.selected.emit(self.cand))
        prev = QToolButton(text="Preview")
        prev.clicked.connect(lambda: self.preview.emit(self.cand))
        src = QToolButton(text="Source")
        src.setEnabled(bool(cand.source_url))
        src.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(cand.source_url)))
        rej = QToolButton(text="✕")
        rej.setToolTip("Reject this candidate")
        rej.clicked.connect(lambda: self.rejected.emit(self.cand))
        for b in (sel, prev, src, rej):
            buttons.addWidget(b)
        lay.addLayout(buttons)

    def set_pixmap(self, pix: QPixmap) -> None:
        if pix.isNull():
            self.image.setText("No preview")
        else:
            self.image.setPixmap(pix)


class CropDialog(QDialog):
    """Lets the user choose a square crop of a non-square image."""

    def __init__(self, data: bytes, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose how to make the artwork square")
        self.img = QImage.fromData(data)
        self.result_choice: tuple[str | None, tuple[int, int, int] | None] = (None, None)
        w, h = self.img.width(), self.img.height()
        self.edge = min(w, h)
        self.horizontal = w > h
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"The image is {w} × {h}. Drag the slider to choose the square area, "
                             "or pad it to a square without cropping. Images are never stretched."))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.preview)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(w, h) - self.edge)
        self.slider.setValue((max(w, h) - self.edge) // 2)
        self.slider.valueChanged.connect(self._redraw)
        lay.addWidget(self.slider)
        box = QDialogButtonBox()
        crop = box.addButton("Use this crop", QDialogButtonBox.AcceptRole)
        pad = box.addButton("Pad to square", QDialogButtonBox.ActionRole)
        box.addButton(QDialogButtonBox.Cancel)
        crop.clicked.connect(self._crop)
        pad.clicked.connect(self._pad)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._redraw()

    def _box(self) -> tuple[int, int, int]:
        off = self.slider.value()
        return (off, 0, self.edge) if self.horizontal else (0, off, self.edge)

    def _redraw(self) -> None:
        scaled = QPixmap.fromImage(self.img.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        factor = scaled.width() / self.img.width()
        left, top, size = self._box()
        p = QPainter(scaled)
        p.setPen(QPen(Qt.yellow, 3))
        p.drawRect(QRect(int(left * factor), int(top * factor), int(size * factor), int(size * factor)))
        p.end()
        self.preview.setPixmap(scaled)

    def _crop(self) -> None:
        self.result_choice = (None, self._box())
        self.accept()

    def _pad(self) -> None:
        self.result_choice = ("pad", None)
        self.accept()


class ChangesDialog(QDialog):
    """Preview of proposed file changes before anything is written."""

    def __init__(self, win: "MainWindow", track_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self.win = win
        self.track_ids = track_ids
        self.setWindowTitle("Review proposed changes")
        self.resize(1000, 520)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Apply mode:"))
        self.mode = QComboBox()
        self.mode.addItem("Prepare copies for Serato (Serato_Analyzed_Music) — recommended",
                          ApplyMode.PREPARE_FOR_SERATO)
        self.mode.addItem("Embed artwork into copies", ApplyMode.EMBED)
        self.mode.addItem("Save artwork separately (audio files not changed)", ApplyMode.SAVE_SEPARATELY)
        top.addWidget(self.mode, 1)
        self.in_place = QCheckBox("Modify original files (backup first)")
        self.in_place.setEnabled(win.ctx.settings.allow_modify_originals)
        self.in_place.setToolTip("Enable 'Allow modifying original files' in Settings to use this.")
        top.addWidget(self.in_place)
        lay.addLayout(top)
        self.output = QLabel()
        lay.addWidget(self.output)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Track", "Action", "Destination", "Artwork", "Warnings", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = self.box.addButton("Apply", QDialogButtonBox.AcceptRole)
        self.box.accepted.connect(self._accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)
        self.mode.currentIndexChanged.connect(self._refresh)
        self.in_place.toggled.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        mode = self.mode.currentData()
        in_place = self.in_place.isChecked() and mode == ApplyMode.EMBED
        self.in_place.setVisible(mode == ApplyMode.EMBED)
        self.plans = self.win.ctx.applier.plan(self.track_ids, mode, in_place)
        self.output.setText(f"Output folder: {self.win.ctx.applier.output_root()}"
                            if mode != ApplyMode.SAVE_SEPARATELY else "Audio files will not be changed.")
        self.table.setRowCount(len(self.plans))
        ready = 0
        for r, plan in enumerate(self.plans):
            row = self.win.ctx.db.get_track(plan.track_id)
            items = [f"{row['artist'] or ''} – {row['title'] or Path(row['path']).name}",
                     plan.action or "—", str(plan.destination or ""), plan.image_size,
                     " ".join(plan.warnings), plan.blocked_reason or "Ready"]
            for c, text in enumerate(items):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                if plan.blocked:
                    it.setForeground(QColor(Qt.gray))
                elif plan.modifies_original or plan.replaces_existing_artwork:
                    it.setForeground(confidence_color("medium"))
                self.table.setItem(r, c, it)
            ready += not plan.blocked
        self.table.resizeColumnsToContents()
        self.apply_btn.setText(f"Apply {ready} change(s)")
        self.apply_btn.setEnabled(ready > 0)

    def _accept(self) -> None:
        if any(p.modifies_original and not p.blocked for p in self.plans):
            n = sum(1 for p in self.plans if p.modifies_original and not p.blocked)
            if QMessageBox.warning(
                    self, "Modify original files?",
                    f"{n} original audio file(s) will be modified. A verified backup of each is made "
                    "first and can be restored from the Reports page.\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.accept()

    def choice(self) -> tuple[ApplyMode, bool, list[int]]:
        mode = self.mode.currentData()
        in_place = self.in_place.isChecked() and mode == ApplyMode.EMBED
        return mode, in_place, [p.track_id for p in self.plans if not p.blocked]


class PreviewDialog(QDialog):
    def __init__(self, data: bytes, cand: ArtworkCandidate, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{cand.release_title or 'Artwork'} — {label(cand.provider)}")
        lay = QVBoxLayout(self)
        img = QImage.fromData(data)
        pic = QLabel()
        pic.setPixmap(QPixmap.fromImage(img.scaled(700, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        lay.addWidget(pic)
        lay.addWidget(QLabel(f"{img.width()} × {img.height()} · {cand.attribution}"))
        note = QLabel(f"<small>{cand.license}</small>")
        note.setWordWrap(True)
        lay.addWidget(note)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


class ArtworkFinderPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.search_job: Job | None = None
        self.scope_label = QLabel("No tracks loaded. Scan a folder or pick a crate.")
        self.model = ArtworkTableModel(win)

        root = QVBoxLayout(self)
        # Scope + batch controls
        top = QHBoxLayout()
        top.addWidget(self.scope_label, 1)
        self.filter = QComboBox()
        self.filter.addItems(["All tracks", "Needing artwork", "Awaiting review", "Approved", "Failed"])
        self.filter.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.filter)
        for text, slot in (("Load folder…", self._load_folder), ("Search artwork", self._search),
                           ("Search again", lambda: self._search(force=True)),
                           ("Retry failed", self._retry_failed)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        top.addWidget(self.pause_btn)
        top.addWidget(self.cancel_btn)
        root.addLayout(top)
        self.progress = QLabel("")
        root.addWidget(self.progress)

        split = QSplitter(Qt.Vertical)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.verticalHeader().setDefaultSectionSize(THUMB + 6)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setColumnWidth(0, 160)
        self.view.setColumnWidth(1, 220)
        self.view.setColumnWidth(2, 160)
        self.view.selectionModel().selectionChanged.connect(self._selection_changed)
        split.addWidget(self.view)

        lower = QWidget()
        low = QVBoxLayout(lower)
        actions = QHBoxLayout()
        self.track_title = QLabel("<b>Select a track to see candidate covers.</b>")
        actions.addWidget(self.track_title, 1)
        for text, slot in (("Search Web for Cover Art", self._web_search),
                           ("Paste image URL…", self._paste_url),
                           ("Import image file…", self._import_file),
                           ("Replace existing artwork…", self._mark_replace),
                           ("Skip track", self._skip),
                           ("Mark as no artwork available", self._no_artwork)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        low.addLayout(actions)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setMinimumHeight(380)
        self.cards_host = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_host)
        self.cards_layout.addStretch(1)
        self.cards_area.setWidget(self.cards_host)
        low.addWidget(self.cards_area)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        review = QPushButton("Review changes and apply…")
        review.clicked.connect(self._review_changes)
        bottom.addWidget(review)
        low.addLayout(bottom)
        split.addWidget(lower)
        split.setSizes([420, 420])
        root.addWidget(split, 1)
        self.card_thumbs = ThumbnailLoader(self)
        self.card_thumbs.loaded.connect(self._card_thumb_loaded)
        self.all_ids: list[int] = []
        self._update_job_buttons()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_tracks(self, track_ids: list[int], scope: str) -> None:
        self.all_ids = list(track_ids)
        self.scope_label.setText(f"<b>{scope}</b> — {len(track_ids)} track(s)")
        self._apply_filter()

    def _apply_filter(self) -> None:
        mode = self.filter.currentIndex()
        ids = self.all_ids
        if mode:
            rows = self.win.ctx.db.artwork_rows(ids)
            needing = set(self.win.ctx.artwork.tracks_needing_artwork(ids)) if mode == 1 else set()
            keep = {
                1: lambda r: r["id"] in needing,
                2: lambda r: r["search_status"] == "found" and r["review_status"] == "pending",
                3: lambda r: r["review_status"] == "approved",
                4: lambda r: bool(r["last_error"]) or "failed" in (r["search_status"], r["download_status"],
                                                                   r["embedding_status"]),
            }[mode]
            ids = [r["id"] for r in rows if keep(r)]
        self.model.set_tracks(ids)
        self._show_candidates(None)

    def _load_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if folder:
            self.win.scan_folder(Path(folder), then_find_artwork=True)

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------
    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        return [self.model.track_at(r) for r in rows]

    def _search(self, force: bool = False, ids: list[int] | None = None) -> None:
        if self.win.ctx.settings.offline_mode:
            QMessageBox.information(self, "Offline mode", "Online artwork search is off (Settings → Offline mode).")
            return
        if ids is None:
            ids = self.selected_ids() or self.win.ctx.artwork.tracks_needing_artwork(self.model.track_ids)
        if not ids:
            QMessageBox.information(self, "Nothing to search", "No tracks in this view need artwork.")
            return
        self.start_search(ids, force)

    def start_search(self, ids: list[int], force: bool = False, interactive: bool = True) -> None:
        if self.search_job and not self.search_job.is_finished:
            msg = "An artwork search is already running; pause or cancel it first."
            if interactive:
                QMessageBox.information(self, "Search running", msg)
            else:
                self.win.statusBar().showMessage(msg, 8000)
            return
        for tid in ids:
            self.win.ctx.db.update_artwork_state(tid, search_status="queued")
            self.model.refresh(tid)
        ctx = self.win.ctx

        def describe(tid):
            r = ctx.db.get_track(tid)
            return f"{r['artist'] or ''} – {r['title'] or Path(r['path']).name}"

        self.search_job = self.win.run_job(Job(
            f"Artwork search ({len(ids)} tracks)", ids,
            lambda tid, job: ctx.artwork.search_track(tid, force=force).status.value,
            kind="artwork_search", describe=describe))
        self._update_job_buttons()

    def _retry_failed(self) -> None:
        rows = self.win.ctx.db.artwork_rows(self.model.track_ids)
        ids = [r["id"] for r in rows if r["search_status"] in ("failed", "offline")
               or r["download_status"] == "failed"]
        if ids:
            self.start_search(ids, force=True)

    def _toggle_pause(self) -> None:
        job = self.search_job
        if not job:
            return
        if job.state == JobState.PAUSED:
            job.resume()
        else:
            job.pause()
        self._update_job_buttons()

    def _cancel(self) -> None:
        if self.search_job:
            self.search_job.cancel()

    def _update_job_buttons(self) -> None:
        running = bool(self.search_job and not self.search_job.is_finished)
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        self.pause_btn.setText("Resume" if running and self.search_job.state == JobState.PAUSED else "Pause")

    def on_job_event(self, ev: JobEvent) -> None:
        job = ev.job
        if ev.kind == "progress" and job.kind in ("artwork_search", "artwork_select", "artwork_apply"):
            tid = ev.result.item if isinstance(ev.result.item, int) else None
            if tid is not None:
                self.model.refresh(tid)
                if tid == self._current_track():
                    self._show_candidates(tid)
        if job is self.search_job and ev.finished and job.done < job.total:
            # Cancelled: tracks that never ran should not stay "Queued".
            for tid in job.items[job.done:]:
                if self.win.ctx.db.get_artwork_state(tid)["search_status"] == "queued":
                    self.win.ctx.db.update_artwork_state(tid, search_status="not_searched")
                    self.model.refresh(tid)
        if job is self.search_job:
            state = job.state.value
            self.progress.setText(f"{job.name}: {job.done}/{job.total} · {state}"
                                  + (f" · {job.current}" if job.current and not job.is_finished else ""))
            self._update_job_buttons()

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------
    def _current_track(self) -> int | None:
        idx = self.view.selectionModel().currentIndex() if self.view.selectionModel() else None
        if idx is None or not idx.isValid():
            sel = self.selected_ids()
            return sel[0] if sel else None
        return self.model.track_at(idx.row())

    def _selection_changed(self, *_):
        self._show_candidates(self._current_track())

    def _show_candidates(self, tid: int | None) -> None:
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards: dict[str, CandidateCard] = {}
        if tid is None:
            self.track_title.setText("<b>Select a track to see candidate covers.</b>")
            return
        row = self.win.ctx.db.artwork_rows([tid])[0]
        msg = row["search_message"] or ""
        self.track_title.setText(f"<b>{row['artist'] or ''} – {row['title'] or Path(row['path']).name}</b>"
                                 f"<br><small>{msg}</small>")
        self.track_title.setWordWrap(True)
        cands = self.win.ctx.artwork.candidates(tid)
        if not cands:
            empty = QLabel("No candidates yet. Search, or use Search Web / Paste image URL / Import image file. "
                           "DJ edits and unofficial remixes often have no public cover.")
            empty.setWordWrap(True)
            self.cards_layout.insertWidget(0, empty)
            return
        for i, cand in enumerate(cands[:12]):
            fetch = (lambda c=cand: self._card_bytes(c))
            card = CandidateCard(cand, self.card_thumbs, fetch)
            card.selected.connect(lambda c, t=tid: self._select(t, c))
            card.rejected.connect(lambda c, t=tid: self._reject(t, c))
            card.preview.connect(self._preview)
            self.cards[card._key] = card
            self.cards_layout.insertWidget(i, card)

    def _card_bytes(self, cand: ArtworkCandidate) -> bytes | None:
        if cand.provider == "manual_file":
            row = self.win.ctx.db.query_one("SELECT path FROM image_cache WHERE source_url = ?", (cand.image_url,))
            return Path(row["path"]).read_bytes() if row else None
        return self.win.ctx.artwork.fetch_thumbnail(cand.provider, cand.thumbnail_url or cand.image_url)

    def _card_thumb_loaded(self, key, img) -> None:
        pix = self.card_thumbs.store(key, img)
        card = getattr(self, "cards", {}).get(key)
        if card:
            card.set_pixmap(pix)

    def _select(self, tid: int, cand: ArtworkCandidate) -> None:
        row = self.win.ctx.db.artwork_rows([tid])[0]
        if row["has_embedded"] and not row["replace_requested"]:
            if QMessageBox.question(
                    self, "Replace existing artwork?",
                    "This track already has embedded artwork. Use the selected image to replace it "
                    "when you apply changes?") != QMessageBox.Yes:
                return
            self.win.ctx.artwork.request_replacement(tid)
        ctx = self.win.ctx

        def work(t, job):
            return ctx.artwork.select_candidate(t, cand.db_id)

        job = self.win.run_job(Job("Download artwork", [tid], work, kind="artwork_select"))
        self._pending_select = (job, cand)

    def after_select(self, job: Job) -> None:
        """Called by the main window when an artwork_select job finishes."""
        if not job.results:
            return
        res = job.results[0]
        tid = res.item
        if not res.ok:
            QMessageBox.warning(self, "Artwork not used", res.message)
        elif res.value is not None and res.value.needs_crop:
            self._ask_crop(tid)
        self.model.refresh(tid)
        if tid == self._current_track():
            self._show_candidates(tid)

    def _ask_crop(self, tid: int) -> None:
        ctx = self.win.ctx
        state = ctx.db.get_artwork_state(tid)
        cand = ctx.artwork.get_candidate(state["selected_candidate_id"])
        try:
            data = (Path(ctx.db.query_one("SELECT path FROM image_cache WHERE source_url = ?",
                                          (cand.image_url,))["path"]).read_bytes())
        except (TypeError, OSError):
            QMessageBox.warning(self, "Crop", "The image is no longer cached; select it again.")
            return
        dlg = CropDialog(data, self)
        if dlg.exec() == QDialog.Accepted:
            mode, box = dlg.result_choice
            res = ctx.artwork.set_crop(tid, mode, box)
            if res.ok:
                self.win.statusBar().showMessage(f"Artwork approved: {res.note}", 8000)
        self.model.refresh(tid)

    def _reject(self, tid: int, cand: ArtworkCandidate) -> None:
        self.win.ctx.artwork.reject_candidate(cand.db_id)
        self.model.refresh(tid)
        self._show_candidates(tid)

    def _preview(self, cand: ArtworkCandidate) -> None:
        ctx = self.win.ctx

        def work(_item, job):
            if cand.provider == "manual_file":
                return self._card_bytes(cand)
            return ctx.artwork.download_image(cand.provider, cand.image_url)

        job = self.win.run_job(Job("Preview artwork", [cand.db_id], work, kind="artwork_preview"))
        self._pending_preview = (job, cand)

    def after_preview(self, job: Job) -> None:
        if not job.results:
            return
        res = job.results[0]
        if not res.ok or not res.value:
            QMessageBox.warning(self, "Preview", res.message or "Could not load the image.")
            return
        PreviewDialog(res.value, self._pending_preview[1], self).exec()

    # ------------------------------------------------------------------
    # Manual actions
    # ------------------------------------------------------------------
    def _need_track(self) -> int | None:
        tid = self._current_track()
        if tid is None:
            QMessageBox.information(self, "Select a track", "Select a track first.")
        return tid

    def _web_search(self) -> None:
        tid = self._need_track()
        if tid is not None:
            QDesktopServices.openUrl(QUrl(self.win.ctx.artwork.web_search_url(tid)))

    def _paste_url(self) -> None:
        tid = self._need_track()
        if tid is None:
            return
        url, ok = QInputDialog.getText(
            self, "Paste image URL",
            "Paste the address of an image you have permission to use (http or https):")
        if not ok or not url.strip():
            return
        try:
            cand = self.win.ctx.artwork.add_manual_url(tid, url.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Invalid URL", str(exc))
            return
        self._show_candidates(tid)
        self._select(tid, cand)

    def _import_file(self) -> None:
        tid = self._need_track()
        if tid is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image", "", "Images (*.jpg *.jpeg *.png)")
        if not path:
            return
        try:
            cand = self.win.ctx.artwork.add_manual_file(tid, Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Image not usable", str(exc))
            return
        self._show_candidates(tid)
        self._select(tid, cand)

    def _for_selected(self, fn) -> None:
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Select tracks", "Select one or more tracks first.")
            return
        for tid in ids:
            fn(tid)
            self.model.refresh(tid)

    def _skip(self) -> None:
        self._for_selected(self.win.ctx.artwork.skip)

    def _no_artwork(self) -> None:
        self._for_selected(self.win.ctx.artwork.mark_no_artwork)

    def _mark_replace(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, "Replace existing artwork",
                                f"Allow new artwork to replace the existing artwork of {len(ids)} track(s)? "
                                "Nothing changes until you review and apply.") == QMessageBox.Yes:
            for tid in ids:
                self.win.ctx.artwork.request_replacement(tid)
                self.model.refresh(tid)

    # ------------------------------------------------------------------
    # Applying
    # ------------------------------------------------------------------
    def _review_changes(self) -> None:
        ids = self.selected_ids() or self.model.track_ids
        rows = self.win.ctx.db.artwork_rows(ids)
        ids = [r["id"] for r in rows if r["review_status"] == "approved"]
        if not ids:
            QMessageBox.information(self, "Nothing approved", "Select artwork for some tracks first.")
            return
        dlg = ChangesDialog(self.win, ids, self)
        if dlg.exec() != QDialog.Accepted:
            return
        mode, in_place, ready = dlg.choice()
        ctx = self.win.ctx

        def work(tid, job):
            [out] = ctx.applier.apply([tid], mode, in_place=in_place, confirmed=in_place)
            if not out.ok:
                raise RuntimeError(out.message)
            return out.message

        job = Job(f"Apply artwork ({len(ready)} tracks)", ready, work, kind="artwork_apply")
        job.apply_mode = mode
        self.win.run_job(job)

    def after_apply(self, job: Job) -> None:
        ok = sum(1 for r in job.results if r.ok)
        failed = len(job.failed)
        text = f"{ok} track(s) updated, {failed} failed."
        if failed:
            text += "\n\n" + "\n".join(r.message for r in job.failed[:10])
        if getattr(job, "apply_mode", None) == ApplyMode.PREPARE_FOR_SERATO and ok:
            text += "\n\n" + SERATO_IMPORT_INSTRUCTIONS
        box = QMessageBox(QMessageBox.Information, "Artwork applied", text, parent=self)
        report = box.addButton("Export report…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is report:
            self.win.export_report(self.all_ids)
        self.model.refresh_all()
