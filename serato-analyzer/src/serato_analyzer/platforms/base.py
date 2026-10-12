"""Platform service interface shared by the macOS, Windows and Linux modules.

Every operating-system specific decision (where settings live, how drives are
enumerated, where Serato keeps its library, how to explain a permission
problem) goes through a :class:`PlatformServices` subclass.  The rest of the
application only ever talks to this interface, so audio analysis, metadata,
artwork discovery and the database stay identical on every platform.

Filesystem probes are injectable so the Windows logic can be unit tested on a
Mac or Linux CI runner and vice versa.
"""

from __future__ import annotations

import importlib
import os
import platform as _platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Callable, Mapping

from .. import APP_NAME

SERATO_DIR_NAME = "_Serato_"
OUTPUT_DIR_NAME = "Serato_Analyzed_Music"


@dataclass(frozen=True)
class Volume:
    """A mounted disk or drive that may hold music."""

    root: PurePath
    label: str
    is_system: bool
    is_removable: bool = False
    is_network: bool = False


@dataclass(frozen=True)
class SeratoLibrary:
    """A ``_Serato_`` database folder.

    Serato stores track paths inside crates relative to the root of the volume
    that holds the ``_Serato_`` folder, so the volume root travels with it.
    """

    root: PurePath
    volume_root: PurePath
    is_system: bool
    label: str = ""

    @property
    def subcrates_dir(self) -> PurePath:
        return self.root / "Subcrates"


@dataclass
class DependencyStatus:
    name: str
    available: bool
    version: str | None = None
    detail: str = ""
    required: bool = True


@dataclass
class AccessCheck:
    path: Path
    exists: bool
    readable: bool
    writable: bool
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.exists and self.readable


def _safe_is_dir(path: PurePath) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


class PlatformServices:
    """Base class.  Subclasses override the OS-specific pieces."""

    name = "generic"
    #: Path flavour used for every path this class builds.  Tests substitute
    #: PureWindowsPath / PurePosixPath to exercise other platforms.
    path_cls: type[PurePath] = Path

    def __init__(
        self,
        home: PurePath | str | None = None,
        env: Mapping[str, str] | None = None,
        is_dir: Callable[[PurePath], bool] | None = None,
        path_cls: type[PurePath] | None = None,
    ) -> None:
        if path_cls is not None:
            self.path_cls = path_cls
        self.env = dict(os.environ if env is None else env)
        self.home = self.path_cls(home) if home is not None else self.path_cls(Path.home())
        self._is_dir = is_dir or _safe_is_dir

    # ------------------------------------------------------------------
    # Application directories
    # ------------------------------------------------------------------
    def config_dir(self) -> PurePath:
        raise NotImplementedError

    def data_dir(self) -> PurePath:
        raise NotImplementedError

    def cache_dir(self) -> PurePath:
        raise NotImplementedError

    def log_dir(self) -> PurePath:
        return self.data_dir() / "Logs"

    def settings_path(self) -> PurePath:
        return self.config_dir() / "settings.json"

    def database_path(self) -> PurePath:
        return self.data_dir() / "library.sqlite3"

    def artwork_cache_dir(self) -> PurePath:
        return self.cache_dir() / "artwork"

    def temp_download_dir(self) -> PurePath:
        """Temporary downloads live in the cache, never next to music files."""
        return self.artwork_cache_dir() / "tmp"

    def backup_dir(self) -> PurePath:
        return self.data_dir() / "backups"

    def default_music_dir(self) -> PurePath:
        return self.home / "Music"

    def default_output_dir(self) -> PurePath:
        return self.default_music_dir() / OUTPUT_DIR_NAME

    def ensure_app_dirs(self) -> None:
        for d in (self.config_dir(), self.data_dir(), self.cache_dir(), self.log_dir(),
                  self.artwork_cache_dir(), self.temp_download_dir(), self.backup_dir()):
            Path(d).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Drives and Serato library discovery
    # ------------------------------------------------------------------
    def system_volume_root(self) -> PurePath:
        raise NotImplementedError

    def list_volumes(self) -> list[Volume]:
        raise NotImplementedError

    def candidate_music_roots(self) -> list[PurePath]:
        """Folders worth offering to the user as music locations.

        The music library is *not* assumed to be on the system drive: every
        mounted volume is offered, along with the user's Music folder.
        """
        roots: list[PurePath] = []
        music = self.default_music_dir()
        if self._is_dir(music):
            roots.append(music)
        for vol in self.list_volumes():
            if not vol.is_system:
                roots.append(vol.root)
        return roots

    def discover_serato_libraries(self) -> list[SeratoLibrary]:
        libs: list[SeratoLibrary] = []
        sys_lib = self.default_music_dir() / SERATO_DIR_NAME
        if self._is_dir(sys_lib):
            libs.append(SeratoLibrary(sys_lib, self.system_volume_root(), True, "System"))
        for vol in self.list_volumes():
            if vol.is_system:
                continue
            candidate = vol.root / SERATO_DIR_NAME
            if self._is_dir(candidate):
                libs.append(SeratoLibrary(candidate, vol.root, False, vol.label))
        return libs

    def resolve_crate_track_path(self, library: SeratoLibrary, stored: str) -> PurePath:
        """Turn a path stored in a crate into an absolute path.

        Serato stores paths relative to the volume root of the library, without
        a leading separator.  Both separators are accepted because libraries
        move between operating systems on external drives.
        """
        stored = stored.strip()
        if len(stored) >= 2 and stored[1] == ":" and stored[0].isalpha():
            # An absolute Windows path (seen in some older libraries).
            return self.path_cls(stored)
        parts = [p for p in stored.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError(f"Refusing crate path containing '..': {stored!r}")
        return library.volume_root.joinpath(*parts)

    def volume_for_path(self, path: PurePath) -> Volume | None:
        best: Volume | None = None
        for vol in self.list_volumes():
            try:
                self.path_cls(path).relative_to(vol.root)
            except ValueError:
                continue
            if best is None or len(str(vol.root)) > len(str(best.root)):
                best = vol
        return best

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------
    def permission_hint(self, path: PurePath) -> str:
        return f"Check that you have permission to read {path}."

    def check_access(self, path: PurePath, probe_write: bool = False) -> AccessCheck:
        p = Path(path)
        exists = p.exists()
        readable = False
        writable = False
        if exists:
            try:
                if p.is_dir():
                    next(iter(p.iterdir()), None)
                else:
                    with p.open("rb") as fh:
                        fh.read(1)
                readable = True
            except OSError:
                readable = False
            if probe_write:
                target = p if p.is_dir() else p.parent
                try:
                    fd, name = tempfile.mkstemp(prefix=".sa-probe-", dir=target)
                    os.close(fd)
                    os.unlink(name)
                    writable = True
                except OSError:
                    writable = False
            else:
                writable = os.access(p, os.W_OK)
        hint = "" if (exists and readable and (writable or not probe_write)) else self.permission_hint(path)
        return AccessCheck(p, exists, readable, writable, hint)

    def open_settings_for_permissions(self) -> str | None:
        """A URL that opens the OS privacy settings, if the OS has one."""
        return None

    # ------------------------------------------------------------------
    # Shell integration
    # ------------------------------------------------------------------
    def reveal_command(self, path: PurePath) -> list[str]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------
    def machine_arch(self) -> str:
        return _platform.machine().lower() or "unknown"

    def detect_dependencies(self) -> list[DependencyStatus]:
        py_ok = sys.version_info >= (3, 10)
        deps = [
            DependencyStatus("Python", py_ok, _platform.python_version(),
                             "" if py_ok else "Python 3.10 or newer is required"),
        ]
        for module, label, required in (
            ("PySide6", "PySide6 (Qt)", True),
            ("mutagen", "mutagen (audio metadata)", True),
            ("PIL", "Pillow (images)", True),
        ):
            deps.append(_module_status(module, label, required))
        ffmpeg = shutil.which("ffmpeg")
        deps.append(DependencyStatus(
            "ffmpeg", ffmpeg is not None, None, ffmpeg or "Not found on PATH (optional)", required=False))
        deps.append(DependencyStatus(
            "Architecture", True, self.machine_arch(), self.architecture_note(), required=False))
        return deps

    def architecture_note(self) -> str:
        return ""


def _module_status(module: str, label: str, required: bool) -> DependencyStatus:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # ImportError, or a broken native library
        return DependencyStatus(label, False, None, f"{type(exc).__name__}: {exc}", required)
    version = (getattr(mod, "__version__", None) or getattr(mod, "version_string", None)
               or getattr(mod, "version", None))
    if not isinstance(version, str):
        version = str(version) if version is not None else None
    return DependencyStatus(label, True, version, "", required)


APP_DIR_NAME = APP_NAME
