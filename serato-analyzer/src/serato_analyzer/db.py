"""SQLite database shared by every platform.

The schema is identical on macOS and Windows; paths are stored as native
strings for the machine that scanned them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY,
    path            TEXT NOT NULL UNIQUE,
    scan_root       TEXT,
    file_size       INTEGER,
    mtime           REAL,
    format          TEXT,
    artist          TEXT,
    title           TEXT,
    album           TEXT,
    album_artist    TEXT,
    year            TEXT,
    label           TEXT,
    catalog_number  TEXT,
    isrc            TEXT,
    mb_release_id   TEXT,
    mb_recording_id TEXT,
    duration        REAL,
    read_error      TEXT,
    added_at        TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS artwork_state (
    track_id            INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    has_embedded        INTEGER NOT NULL DEFAULT 0,
    embedded_hash       TEXT,
    embedded_width      INTEGER,
    embedded_height     INTEGER,
    embedded_mime       TEXT,
    artwork_status      TEXT NOT NULL DEFAULT 'unknown',
    replace_requested   INTEGER NOT NULL DEFAULT 0,
    search_status       TEXT NOT NULL DEFAULT 'not_searched',
    search_message      TEXT,
    review_status       TEXT NOT NULL DEFAULT 'pending',
    last_search_at      TEXT,
    providers_queried   TEXT,
    selected_candidate_id INTEGER,
    selected_image_path TEXT,
    selected_image_hash TEXT,
    selected_source     TEXT,
    download_status     TEXT NOT NULL DEFAULT 'none',
    embedding_status    TEXT NOT NULL DEFAULT 'none',
    apply_mode          TEXT,
    applied_path        TEXT,
    applied_image_hash  TEXT,
    backup_path         TEXT,
    last_error          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS artwork_candidates (
    id              INTEGER PRIMARY KEY,
    track_id        INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    image_url       TEXT NOT NULL,
    thumbnail_url   TEXT,
    full_image_url  TEXT,
    width           INTEGER,
    height          INTEGER,
    release_id      TEXT,
    release_title   TEXT,
    release_artist  TEXT,
    recording_title TEXT,
    release_date    TEXT,
    release_type    TEXT,
    label           TEXT,
    catalog_number  TEXT,
    isrc            TEXT,
    source_url      TEXT,
    attribution     TEXT,
    license         TEXT,
    score           REAL,
    confidence      TEXT,
    reasons         TEXT,
    rejected        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT,
    UNIQUE (track_id, provider, image_url)
);

CREATE TABLE IF NOT EXISTS provider_cache (
    key         TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    payload     TEXT NOT NULL,
    fetched_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_cache (
    hash         TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    source_url   TEXT,
    width        INTEGER,
    height       INTEGER,
    mime         TEXT,
    size_bytes   INTEGER,
    fetched_at   REAL,
    last_used_at REAL
);

CREATE TABLE IF NOT EXISTS backups (
    id            INTEGER PRIMARY KEY,
    track_id      INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
    original_path TEXT NOT NULL,
    backup_path   TEXT NOT NULL,
    original_hash TEXT NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL,
    restored_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidates_track ON artwork_candidates(track_id);
CREATE INDEX IF NOT EXISTS idx_tracks_scan_root ON tracks(scan_root);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------
    TRACK_FIELDS = ("scan_root", "file_size", "mtime", "format", "artist", "title", "album",
                    "album_artist", "year", "label", "catalog_number", "isrc", "mb_release_id",
                    "mb_recording_id", "duration", "read_error")

    def upsert_track(self, path: str, **values: Any) -> int:
        now = utcnow()
        cols = [k for k in self.TRACK_FIELDS if k in values]
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM tracks WHERE path = ?", (path,)).fetchone()
            if row:
                if cols:
                    assignments = ", ".join(f"{c} = ?" for c in cols)
                    conn.execute(f"UPDATE tracks SET {assignments}, updated_at = ? WHERE id = ?",
                                 [values[c] for c in cols] + [now, row["id"]])
                track_id = row["id"]
            else:
                names = ", ".join(["path", *cols, "added_at", "updated_at"])
                marks = ", ".join("?" * (len(cols) + 3))
                cur = conn.execute(f"INSERT INTO tracks ({names}) VALUES ({marks})",
                                   [path, *[values[c] for c in cols], now, now])
                track_id = cur.lastrowid
            conn.execute("INSERT OR IGNORE INTO artwork_state (track_id, updated_at) VALUES (?, ?)",
                         (track_id, now))
        return int(track_id)

    def get_track(self, track_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM tracks WHERE id = ?", (track_id,))

    def get_track_by_path(self, path: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM tracks WHERE path = ?", (path,))

    def track_ids_for_paths(self, paths: Iterable[str]) -> list[int]:
        ids = []
        for p in paths:
            row = self.get_track_by_path(p)
            if row:
                ids.append(int(row["id"]))
        return ids

    # ------------------------------------------------------------------
    # Artwork state
    # ------------------------------------------------------------------
    STATE_FIELDS = frozenset((
        "has_embedded", "embedded_hash", "embedded_width", "embedded_height", "embedded_mime",
        "artwork_status", "replace_requested", "search_status", "search_message",
        "review_status", "last_search_at", "providers_queried", "selected_candidate_id",
        "selected_image_path", "selected_image_hash", "selected_source", "download_status",
        "embedding_status", "apply_mode", "applied_path", "applied_image_hash", "backup_path",
        "last_error",
    ))

    def update_artwork_state(self, track_id: int, **values: Any) -> None:
        unknown = set(values) - self.STATE_FIELDS
        if unknown:
            raise KeyError(f"Unknown artwork_state fields: {sorted(unknown)}")
        if "providers_queried" in values and not isinstance(values["providers_queried"], (str, type(None))):
            values["providers_queried"] = json.dumps(values["providers_queried"])
        values["updated_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in values)
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO artwork_state (track_id) VALUES (?)", (track_id,))
            conn.execute(f"UPDATE artwork_state SET {assignments} WHERE track_id = ?",
                         [*values.values(), track_id])

    def get_artwork_state(self, track_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM artwork_state WHERE track_id = ?", (track_id,))

    def artwork_rows(self, track_ids: Iterable[int] | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT t.*, s.*, t.id AS id FROM tracks t "
               "JOIN artwork_state s ON s.track_id = t.id")
        if track_ids is None:
            return self.query(sql + " ORDER BY t.artist COLLATE NOCASE, t.title COLLATE NOCASE")
        ids = list(track_ids)
        if not ids:
            return []
        rows: list[sqlite3.Row] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += self.query(sql + f" WHERE t.id IN ({','.join('?' * len(chunk))})", chunk)
        order = {tid: n for n, tid in enumerate(ids)}
        rows.sort(key=lambda r: order.get(r["id"], 0))
        return rows

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self.query_one(sql)
            return int(row[0]) if row else 0
        return {
            "tracks": one("SELECT COUNT(*) FROM tracks"),
            "missing_artwork": one("SELECT COUNT(*) FROM artwork_state WHERE artwork_status = 'missing'"),
            "low_quality_artwork": one(
                "SELECT COUNT(*) FROM artwork_state WHERE artwork_status IN ('low_resolution', 'non_square')"),
            "awaiting_review": one(
                "SELECT COUNT(*) FROM artwork_state WHERE search_status = 'found' AND review_status = 'pending'"),
            "approved": one("SELECT COUNT(*) FROM artwork_state WHERE review_status = 'approved'"),
            "applied": one("SELECT COUNT(*) FROM artwork_state WHERE review_status = 'applied'"),
            "failed": one("SELECT COUNT(*) FROM artwork_state WHERE search_status = 'failed' "
                          "OR embedding_status = 'failed' OR download_status = 'failed'"),
        }
