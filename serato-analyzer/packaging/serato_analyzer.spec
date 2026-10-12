# PyInstaller spec shared by the macOS and Windows builds.
#
# Build on the platform you are targeting (PyInstaller does not cross-compile):
#   macOS:   packaging/macos/build_macos.sh
#   Windows: packaging\windows\build_windows.ps1
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from serato_analyzer import APP_DISPLAY_NAME, __version__  # noqa: E402

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
EXE_NAME = APP_DISPLAY_NAME if IS_MAC else "SeratoAnalyzer"

# Qt modules the app does not use; excluding them keeps the bundle small.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "tkinter", "unittest", "pytest", "numpy",
]
# Libraries/plugins pulled in only by Qt plugins we do not need (virtual
# keyboard, PDF image format) together with the Quick/QML stack they require.
DROP_BINARIES = ("qt6quick", "qt6qml", "qt6pdf", "virtualkeyboard", "qpdf", "qtquick", "qtqml")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    hiddenimports=collect_submodules("mutagen") + collect_submodules("serato_analyzer"),
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if not any(k in b[0].lower().replace("\\", "/") for k in DROP_BINARIES)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    console=False,
    # macOS: "arm64", "x86_64" or "universal2" (universal2 needs a universal2
    # Python *and* universal2 wheels for every dependency).
    target_arch=os.environ.get("TARGET_ARCH") or None,
    codesign_identity=os.environ.get("CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("CODESIGN_ENTITLEMENTS") or None,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if IS_WIN else None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="SeratoAnalyzer")

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_DISPLAY_NAME}.app",
        bundle_identifier="com.deanhome.seratoanalyzer",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.music",
            # Shown by macOS when the app first reads these locations.
            "NSRemovableVolumesUsageDescription":
                "Serato Analyzer reads music libraries on external drives.",
            "NSNetworkVolumesUsageDescription":
                "Serato Analyzer reads music libraries on network drives.",
            "NSDocumentsFolderUsageDescription":
                "Serato Analyzer reads music you keep in Documents.",
            "NSDesktopFolderUsageDescription":
                "Serato Analyzer reads music you keep on the Desktop.",
            "NSDownloadsFolderUsageDescription":
                "Serato Analyzer reads newly downloaded music.",
        },
    )
