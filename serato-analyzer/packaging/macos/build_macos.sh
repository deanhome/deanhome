#!/usr/bin/env bash
# Build "Serato Analyzer.app" and (optionally) a .dmg on a Mac.
#
#   packaging/macos/build_macos.sh                 # native arch (arm64 on Apple Silicon)
#   TARGET_ARCH=x86_64 packaging/macos/build_macos.sh   # Intel build (needs an x86_64 Python)
#   MAKE_DMG=0 packaging/macos/build_macos.sh      # skip the .dmg
#   CODESIGN_IDENTITY="Developer ID Application: …" packaging/macos/build_macos.sh
#
# Build each architecture on a Mac (or CI runner) of that architecture; see README.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
ARCH="${TARGET_ARCH:-$(uname -m)}"
VERSION="$("$PYTHON" -c 'import sys; sys.path.insert(0, "src"); import serato_analyzer as s; print(s.__version__)')"
DIST="dist/macos-$ARCH"
APP="$DIST/Serato Analyzer.app"

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -e ".[build,dev]"

if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  QT_QPA_PLATFORM=offscreen "$PYTHON" -m pytest -q
fi

export TARGET_ARCH="$ARCH"
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/serato_analyzer.spec \
  --distpath "$DIST" --workpath "build/macos-$ARCH"

echo "Built architectures:"
lipo -archs "$APP/Contents/MacOS/Serato Analyzer"

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --deep --force --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

# The bundle must load Qt, mutagen and Pillow on this machine.
"$APP/Contents/MacOS/Serato Analyzer" --self-test

if [[ "${MAKE_DMG:-1}" == "1" ]]; then
  STAGING="build/dmg-$ARCH"
  rm -rf "$STAGING"
  mkdir -p "$STAGING"
  cp -R "$APP" "$STAGING/"
  ln -s /Applications "$STAGING/Applications"
  DMG="dist/SeratoAnalyzer-$VERSION-macos-$ARCH.dmg"
  rm -f "$DMG"
  hdiutil create -volname "Serato Analyzer" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
  if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    codesign --force --sign "$CODESIGN_IDENTITY" "$DMG"
  fi
  echo "DMG: $DMG"
fi
echo "App: $APP"
