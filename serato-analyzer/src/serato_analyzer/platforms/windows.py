"""Windows platform services."""

from __future__ import annotations

import string
import sys
from pathlib import PurePath
from typing import Callable

from .base import APP_DIR_NAME, PlatformServices, Volume

# GetDriveTypeW return values
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


def _default_drive_lister() -> list[tuple[str, int, str]]:
    """Return ``(letter, drive_type, label)`` for each mounted drive."""
    if sys.platform != "win32":
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives: list[tuple[str, int, str]] = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        label_buf = ctypes.create_unicode_buffer(261)
        # Avoid "insert disk" dialogs for empty card readers / optical drives.
        old_mode = kernel32.SetErrorMode(1)
        try:
            ok = kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), label_buf, 261,
                                                None, None, None, None, 0)
        finally:
            kernel32.SetErrorMode(old_mode)
        if not ok and dtype in (DRIVE_REMOVABLE, DRIVE_CDROM):
            continue  # no media inserted
        drives.append((letter, dtype, label_buf.value if ok else ""))
    return drives


def _known_folder_music() -> str | None:
    """The user's Music folder, honouring OneDrive / custom redirection."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("{4BD8D571-6D19-48D3-BE97-422220080E43}")  # FOLDERID_Music
        guid = GUID(u.fields[0], u.fields[1], u.fields[2],
                    (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        out = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if res != 0:
            return None
        value = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return value
    except Exception:
        return None


class WindowsPlatform(PlatformServices):
    name = "windows"

    def __init__(self, *args,
                 drive_lister: Callable[[], list[tuple[str, int, str]]] | None = None,
                 music_dir_lookup: Callable[[], str | None] | None = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._drive_lister = drive_lister or _default_drive_lister
        self._music_dir_lookup = music_dir_lookup or _known_folder_music

    # Directories ---------------------------------------------------------
    def _appdata(self) -> PurePath:
        value = self.env.get("APPDATA")
        return self.path_cls(value) if value else self.home / "AppData" / "Roaming"

    def _local_appdata(self) -> PurePath:
        value = self.env.get("LOCALAPPDATA")
        return self.path_cls(value) if value else self.home / "AppData" / "Local"

    def config_dir(self) -> PurePath:
        # Settings roam with the user profile.
        return self._appdata() / APP_DIR_NAME

    def data_dir(self) -> PurePath:
        # The database and backups are machine-local (they reference local drives).
        return self._local_appdata() / APP_DIR_NAME

    def cache_dir(self) -> PurePath:
        return self._local_appdata() / APP_DIR_NAME / "Cache"

    def default_music_dir(self) -> PurePath:
        found = self._music_dir_lookup()
        return self.path_cls(found) if found else self.home / "Music"

    # Volumes -------------------------------------------------------------
    def _system_letter(self) -> str:
        drive = self.env.get("SystemDrive", "C:")
        return (drive[:1] or "C").upper()

    def system_volume_root(self) -> PurePath:
        return self.path_cls(f"{self._system_letter()}:\\")

    def list_volumes(self) -> list[Volume]:
        system = self._system_letter()
        vols: list[Volume] = []
        for letter, dtype, label in self._drive_lister():
            letter = letter.upper()
            if dtype == DRIVE_CDROM:
                continue
            vols.append(Volume(
                root=self.path_cls(f"{letter}:\\"),
                label=label or f"{letter}:",
                is_system=letter == system,
                is_removable=dtype == DRIVE_REMOVABLE,
                is_network=dtype == DRIVE_REMOTE,
            ))
        if not any(v.is_system for v in vols):
            vols.insert(0, Volume(self.system_volume_root(), f"{system}:", True))
        return vols

    # Permissions ---------------------------------------------------------
    def permission_hint(self, path: PurePath) -> str:
        return (f"Windows denied access to {path}. Check the folder's Security permissions, "
                "that the file is not open in another program (such as Serato), and that "
                "Controlled Folder Access in Windows Security allows this app.")

    def open_settings_for_permissions(self) -> str | None:
        return "windowsdefender://ransomwareprotection"

    # Shell ---------------------------------------------------------------
    def reveal_command(self, path: PurePath) -> list[str]:
        return ["explorer", f"/select,{path}"]

    def architecture_note(self) -> str:
        arch = (self.env.get("PROCESSOR_ARCHITEW6432") or self.env.get("PROCESSOR_ARCHITECTURE") or "").upper()
        if arch == "ARM64" and self.machine_arch() in ("amd64", "x86_64"):
            return "x64 build running under emulation on Windows on ARM."
        if arch == "ARM64":
            return "Windows on ARM (native arm64)"
        return "Intel/AMD x64" if arch in ("AMD64", "") else arch
