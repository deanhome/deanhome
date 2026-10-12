"""Linux platform services.

Linux is not a shipping target (Serato DJ runs on macOS and Windows), but the
application runs here for development and CI.
"""

from __future__ import annotations

from pathlib import Path, PurePath

from .base import APP_DIR_NAME, PlatformServices, Volume


class LinuxPlatform(PlatformServices):
    name = "linux"

    def _xdg(self, var: str, fallback: PurePath) -> PurePath:
        value = self.env.get(var)
        return self.path_cls(value) if value else fallback

    def config_dir(self) -> PurePath:
        return self._xdg("XDG_CONFIG_HOME", self.home / ".config") / APP_DIR_NAME

    def data_dir(self) -> PurePath:
        return self._xdg("XDG_DATA_HOME", self.home / ".local" / "share") / APP_DIR_NAME

    def cache_dir(self) -> PurePath:
        return self._xdg("XDG_CACHE_HOME", self.home / ".cache") / APP_DIR_NAME

    def log_dir(self) -> PurePath:
        return self._xdg("XDG_STATE_HOME", self.home / ".local" / "state") / APP_DIR_NAME / "logs"

    def system_volume_root(self) -> PurePath:
        return self.path_cls("/")

    def list_volumes(self) -> list[Volume]:
        vols = [Volume(self.path_cls("/"), "Root", True)]
        user = self.env.get("USER", "")
        for base in (Path("/media") / user, Path("/run/media") / user, Path("/mnt")):
            try:
                children = sorted(base.iterdir()) if user or base.name == "mnt" else []
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    vols.append(Volume(self.path_cls(child), child.name, False, True))
        return vols

    def reveal_command(self, path: PurePath) -> list[str]:
        return ["xdg-open", str(Path(path).parent)]
