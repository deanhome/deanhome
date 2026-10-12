"""Wires the shared services together for the GUI, the CLI and tests."""

from __future__ import annotations

from pathlib import Path

from .artwork.apply import ArtworkApplier
from .artwork.backups import BackupManager
from .artwork.http import Transport
from .artwork.service import ArtworkService
from .config import Settings
from .db import Database
from .jobs import JobRunner
from .platforms import PlatformServices, current_platform
from .scanner import LibraryScanner


class AppContext:
    def __init__(self, platform: PlatformServices | None = None, settings: Settings | None = None,
                 transport: Transport | None = None, db_path: Path | str | None = None) -> None:
        self.platform = platform or current_platform()
        self.platform.ensure_app_dirs()
        self.settings_path = Path(self.platform.settings_path())
        self.settings = settings or Settings.load(self.settings_path)
        self.db = Database(db_path or self.platform.database_path())
        self.artwork = ArtworkService.create(self.platform, self.db, self.settings, transport)
        self.backups = BackupManager(Path(self.platform.backup_dir()), self.db)
        self.applier = ArtworkApplier(self.db, self.settings, self.platform, self.backups,
                                      Path(self.platform.data_dir()) / "saved_artwork")
        self.scanner = LibraryScanner(self.db, self.artwork, self.platform)
        self.jobs = JobRunner()

    def save_settings(self, settings: Settings) -> None:
        settings.save(self.settings_path)
        self.settings = settings
        self.artwork.apply_settings(settings)
        self.applier.settings = settings

    def close(self) -> None:
        self.jobs.cancel_all()
        self.db.close()
