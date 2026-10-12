"""Platform-specific services (drives, app directories, permissions, dependencies)."""

from __future__ import annotations

import sys
from functools import lru_cache

from .base import (
    OUTPUT_DIR_NAME,
    SERATO_DIR_NAME,
    AccessCheck,
    DependencyStatus,
    PlatformServices,
    SeratoLibrary,
    Volume,
)
from .linux import LinuxPlatform
from .macos import MacOSPlatform
from .windows import WindowsPlatform


def platform_class(sys_platform: str | None = None) -> type[PlatformServices]:
    sp = sys_platform or sys.platform
    if sp == "darwin":
        return MacOSPlatform
    if sp.startswith("win"):
        return WindowsPlatform
    return LinuxPlatform


@lru_cache(maxsize=1)
def current_platform() -> PlatformServices:
    return platform_class()()


__all__ = [
    "AccessCheck", "DependencyStatus", "LinuxPlatform", "MacOSPlatform", "OUTPUT_DIR_NAME",
    "PlatformServices", "SERATO_DIR_NAME", "SeratoLibrary", "Volume", "WindowsPlatform",
    "current_platform", "platform_class",
]
