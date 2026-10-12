"""macOS platform services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePath
from typing import Callable

from .base import APP_DIR_NAME, PlatformServices, Volume


def _default_volume_entries(volumes_root: PurePath) -> list[tuple[PurePath, bool]]:
    """Return ``(mount_point, is_system)`` for each entry in /Volumes.

    The startup disk appears in /Volumes as a symlink to ``/``.
    """
    entries: list[tuple[PurePath, bool]] = []
    root = Path(volumes_root)
    try:
        children = sorted(root.iterdir())
    except OSError:
        return entries
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if not child.is_dir():
                continue
            is_system = child.resolve() == Path("/")
        except OSError:
            continue
        entries.append((child, is_system))
    return entries


class MacOSPlatform(PlatformServices):
    name = "macos"

    def __init__(self, *args, volumes_root: PurePath | str = "/Volumes",
                 volume_lister: Callable[[PurePath], list[tuple[PurePath, bool]]] | None = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.volumes_root = self.path_cls(volumes_root)
        self._volume_lister = volume_lister or _default_volume_entries

    # Directories ---------------------------------------------------------
    def _library(self) -> PurePath:
        return self.home / "Library"

    def config_dir(self) -> PurePath:
        return self._library() / "Application Support" / APP_DIR_NAME

    def data_dir(self) -> PurePath:
        # Settings and the SQLite database both live in Application Support.
        return self.config_dir()

    def cache_dir(self) -> PurePath:
        return self._library() / "Caches" / APP_DIR_NAME

    def log_dir(self) -> PurePath:
        return self._library() / "Logs" / APP_DIR_NAME

    # Volumes -------------------------------------------------------------
    def system_volume_root(self) -> PurePath:
        return self.path_cls("/")

    def list_volumes(self) -> list[Volume]:
        vols: list[Volume] = []
        for mount, is_system in self._volume_lister(self.volumes_root):
            mount = self.path_cls(mount)
            vols.append(Volume(
                root=self.path_cls("/") if is_system else mount,
                label=mount.name,
                is_system=is_system,
                is_removable=not is_system,
            ))
        if not any(v.is_system for v in vols):
            vols.insert(0, Volume(self.path_cls("/"), "Startup Disk", True))
        return vols

    # Permissions ---------------------------------------------------------
    def permission_hint(self, path: PurePath) -> str:
        p = str(path)
        if p.startswith(str(self.volumes_root)):
            where = "Removable Volumes (or Network Volumes)"
        elif any(seg in p for seg in ("/Desktop", "/Documents", "/Downloads")):
            where = "Files and Folders"
        else:
            where = "Files and Folders or Full Disk Access"
        return (f"macOS blocked access to {path}. Choose the folder with the Open button so "
                f"macOS can grant access, or allow {where} for this app in System Settings > "
                "Privacy & Security.")

    def open_settings_for_permissions(self) -> str | None:
        return "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"

    # Shell ---------------------------------------------------------------
    def reveal_command(self, path: PurePath) -> list[str]:
        return ["open", "-R", str(path)]

    # Dependencies --------------------------------------------------------
    def architecture_note(self) -> str:
        arch = self.machine_arch()
        if arch == "x86_64" and _running_under_rosetta():
            return "Intel build running under Rosetta 2 on Apple Silicon; a native arm64 build is faster."
        if arch == "arm64":
            return "Apple Silicon (native arm64)"
        if arch == "x86_64":
            return "Intel Mac"
        return ""


def _running_under_rosetta() -> bool:
    if os.uname().sysname != "Darwin":
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False
