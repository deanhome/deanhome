"""Verified backups of audio files before they are modified, and restore."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..db import Database, utcnow
from ..fsutil import copy_verified, safe_filename, sha256_file, unique_path


class BackupError(Exception):
    pass


class BackupManager:
    def __init__(self, root: Path, db: Database) -> None:
        self.root = Path(root)
        self.db = db

    def backup(self, path: Path, track_id: int | None = None, reason: str = "") -> Path:
        path = Path(path)
        day_dir = self.root / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(path)
        dest = unique_path(day_dir / f"{digest[:12]}_{safe_filename(path.name, max_bytes=120)}")
        copied = copy_verified(path, dest)
        if copied != digest:
            raise BackupError(f"{path} changed while it was being backed up")
        self.db.execute(
            "INSERT INTO backups (track_id, original_path, backup_path, original_hash, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (track_id, str(path), str(dest), digest, reason, utcnow()))
        return dest

    def list(self, track_id: int | None = None):
        if track_id is None:
            return self.db.query("SELECT * FROM backups ORDER BY id DESC")
        return self.db.query("SELECT * FROM backups WHERE track_id = ? ORDER BY id DESC", (track_id,))

    def restore(self, backup_id: int, destination: Path | None = None) -> Path:
        row = self.db.query_one("SELECT * FROM backups WHERE id = ?", (backup_id,))
        if row is None:
            raise BackupError(f"No backup with id {backup_id}")
        src = Path(row["backup_path"])
        if not src.is_file():
            raise BackupError(f"Backup file is missing: {src}")
        if sha256_file(src) != row["original_hash"]:
            raise BackupError(f"Backup file is damaged (hash mismatch): {src}")
        dest = Path(destination or row["original_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_verified(src, dest)
        self.db.execute("UPDATE backups SET restored_at = ? WHERE id = ?", (utcnow(), backup_id))
        return dest
