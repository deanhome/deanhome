"""Platform-dependent paths, drives and Serato discovery for macOS and Windows.

These run on every CI host: pure path flavours plus injected filesystem probes
let the Windows logic be checked on a Mac and vice versa.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from serato_analyzer.platforms import (
    LinuxPlatform, MacOSPlatform, SeratoLibrary, WindowsPlatform, platform_class,
)
from serato_analyzer.platforms.macos import filesystem_root
from serato_analyzer.platforms.windows import DRIVE_CDROM, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_REMOVABLE


def test_platform_selection():
    assert platform_class("darwin") is MacOSPlatform
    assert platform_class("win32") is WindowsPlatform
    assert platform_class("linux") is LinuxPlatform


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

def mac(volumes=(), existing=()):
    existing = {PurePosixPath(p) for p in existing}
    return MacOSPlatform(
        home="/Users/dj", path_cls=PurePosixPath, env={},
        is_dir=lambda p: PurePosixPath(p) in existing,
        volume_lister=lambda root: [(PurePosixPath(root) / name, is_sys) for name, is_sys in volumes],
    )


def test_macos_app_dirs():
    p = mac()
    assert p.settings_path() == PurePosixPath("/Users/dj/Library/Application Support/SeratoAnalyzer/settings.json")
    assert p.database_path() == PurePosixPath("/Users/dj/Library/Application Support/SeratoAnalyzer/library.sqlite3")
    assert p.cache_dir() == PurePosixPath("/Users/dj/Library/Caches/SeratoAnalyzer")
    assert p.temp_download_dir().is_relative_to(p.cache_dir())
    assert p.default_output_dir() == PurePosixPath("/Users/dj/Music/Serato_Analyzed_Music")


def test_macos_external_volumes_and_serato_libraries():
    p = mac(volumes=[("Macintosh HD", True), ("DJ Music", False), ("Backup", False)],
            existing=["/Users/dj/Music/_Serato_", "/Volumes/DJ Music/_Serato_"])
    vols = p.list_volumes()
    assert [v.label for v in vols] == ["Macintosh HD", "DJ Music", "Backup"]
    assert vols[0].is_system and vols[0].root == PurePosixPath("/")
    assert vols[1].root == PurePosixPath("/Volumes/DJ Music") and vols[1].is_removable

    libs = p.discover_serato_libraries()
    assert [(str(l.root), str(l.volume_root), l.is_system) for l in libs] == [
        ("/Users/dj/Music/_Serato_", "/", True),
        ("/Volumes/DJ Music/_Serato_", "/Volumes/DJ Music", False),
    ]


def test_macos_music_not_assumed_on_system_drive():
    p = mac(volumes=[("Macintosh HD", True), ("DJ Music", False)], existing=[])
    assert PurePosixPath("/Volumes/DJ Music") in p.candidate_music_roots()


def test_macos_crate_path_resolution():
    p = mac()
    ext = SeratoLibrary(PurePosixPath("/Volumes/DJ Music/_Serato_"), PurePosixPath("/Volumes/DJ Music"), False)
    sys_lib = SeratoLibrary(PurePosixPath("/Users/dj/Music/_Serato_"), PurePosixPath("/"), True)
    assert p.resolve_crate_track_path(ext, "House/Track 1.mp3") == PurePosixPath("/Volumes/DJ Music/House/Track 1.mp3")
    assert p.resolve_crate_track_path(sys_lib, "Users/dj/Music/a.mp3") == PurePosixPath("/Users/dj/Music/a.mp3")
    with pytest.raises(ValueError):
        p.resolve_crate_track_path(ext, "../../etc/passwd")


def test_macos_permission_hint_mentions_privacy_settings():
    hint = mac().permission_hint(PurePosixPath("/Volumes/DJ Music/House"))
    assert "Privacy & Security" in hint and "Removable Volumes" in hint


def test_macos_real_volume_listing(tmp_path):
    vols_root = tmp_path / "Volumes"
    (vols_root / "DJ Music").mkdir(parents=True)
    (vols_root / ".hidden").mkdir()
    # The startup disk appears in /Volumes as a directory symlink to the root.
    (vols_root / "Macintosh HD").symlink_to(filesystem_root(), target_is_directory=True)
    p = MacOSPlatform(home=tmp_path / "home", env={}, volumes_root=vols_root)
    vols = {v.label: v for v in p.list_volumes()}
    assert vols["Macintosh HD"].is_system
    assert not vols["DJ Music"].is_system
    assert ".hidden" not in vols


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

WIN_ENV = {"APPDATA": r"C:\Users\dj\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\dj\AppData\Local",
           "SystemDrive": "C:", "PROCESSOR_ARCHITECTURE": "AMD64"}


def win(drives=(), existing=(), music=None, env=WIN_ENV):
    existing = {PureWindowsPath(p) for p in existing}
    return WindowsPlatform(
        home=r"C:\Users\dj", path_cls=PureWindowsPath, env=env,
        is_dir=lambda p: PureWindowsPath(p) in existing,
        drive_lister=lambda: list(drives),
        music_dir_lookup=lambda: music,
    )


def test_windows_app_dirs():
    p = win()
    assert p.settings_path() == PureWindowsPath(r"C:\Users\dj\AppData\Roaming\SeratoAnalyzer\settings.json")
    assert p.database_path() == PureWindowsPath(r"C:\Users\dj\AppData\Local\SeratoAnalyzer\library.sqlite3")
    assert p.cache_dir() == PureWindowsPath(r"C:\Users\dj\AppData\Local\SeratoAnalyzer\Cache")
    assert p.default_output_dir() == PureWindowsPath(r"C:\Users\dj\Music\Serato_Analyzed_Music")


def test_windows_app_dirs_fallback_without_env():
    p = win(env={})
    assert p.data_dir() == PureWindowsPath(r"C:\Users\dj\AppData\Local\SeratoAnalyzer")


def test_windows_redirected_music_folder():
    p = win(music=r"D:\OneDrive\Music")
    assert p.default_music_dir() == PureWindowsPath(r"D:\OneDrive\Music")


def test_windows_drive_letters_and_removable():
    p = win(drives=[("C", DRIVE_FIXED, "Windows"), ("D", DRIVE_FIXED, "DJ Music"),
                    ("E", DRIVE_REMOVABLE, "USB"), ("F", DRIVE_CDROM, ""), ("Z", DRIVE_REMOTE, "NAS")])
    vols = {str(v.root): v for v in p.list_volumes()}
    assert set(vols) == {"C:\\", "D:\\", "E:\\", "Z:\\"}
    assert vols["C:\\"].is_system and not vols["D:\\"].is_system
    assert vols["E:\\"].is_removable and vols["Z:\\"].is_network


def test_windows_non_c_system_drive():
    env = dict(WIN_ENV, SystemDrive="E:")
    p = win(drives=[("C", DRIVE_FIXED, "Data"), ("E", DRIVE_FIXED, "OS")], env=env)
    vols = {str(v.root): v for v in p.list_volumes()}
    assert vols["E:\\"].is_system and not vols["C:\\"].is_system


def test_windows_serato_discovery_on_external_drive():
    p = win(drives=[("C", DRIVE_FIXED, "Windows"), ("D", DRIVE_FIXED, "DJ Music")],
            existing=[r"C:\Users\dj\Music\_Serato_", r"D:\_Serato_"])
    libs = p.discover_serato_libraries()
    assert [(str(l.root), str(l.volume_root)) for l in libs] == [
        (r"C:\Users\dj\Music\_Serato_", "C:\\"), (r"D:\_Serato_", "D:\\")]


def test_windows_crate_path_resolution():
    p = win()
    d = SeratoLibrary(PureWindowsPath(r"D:\_Serato_"), PureWindowsPath("D:\\"), False)
    assert p.resolve_crate_track_path(d, "DJ Music/House/a.mp3") == PureWindowsPath(r"D:\DJ Music\House\a.mp3")
    assert p.resolve_crate_track_path(d, r"DJ Music\House\a.mp3") == PureWindowsPath(r"D:\DJ Music\House\a.mp3")
    assert p.resolve_crate_track_path(d, r"E:\Other\b.mp3") == PureWindowsPath(r"E:\Other\b.mp3")


def test_windows_permission_hint():
    hint = win().permission_hint(PureWindowsPath(r"D:\DJ Music"))
    assert "Controlled Folder Access" in hint


def test_windows_arm_detection():
    p = win(env=dict(WIN_ENV, PROCESSOR_ARCHITECTURE="ARM64"))
    assert "ARM" in p.architecture_note()


# ---------------------------------------------------------------------------
# Real filesystem (whatever OS runs the tests)
# ---------------------------------------------------------------------------

def test_ensure_app_dirs_and_access(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_DATA_HOME": str(tmp_path / "d"),
           "XDG_CACHE_HOME": str(tmp_path / "k"), "XDG_STATE_HOME": str(tmp_path / "s")}
    p = LinuxPlatform(home=tmp_path, env=env)
    p.ensure_app_dirs()
    assert Path(p.backup_dir()).is_dir() and Path(p.temp_download_dir()).is_dir()
    check = p.check_access(tmp_path, probe_write=True)
    assert check.ok and check.writable
    missing = p.check_access(tmp_path / "nope")
    assert not missing.ok and missing.hint


def test_dependency_detection_reports_required_modules():
    deps = {d.name: d for d in LinuxPlatform(env={}).detect_dependencies()}
    assert deps["mutagen (audio metadata)"].available
    assert deps["Pillow (images)"].available
    assert deps["ffmpeg"].required is False
