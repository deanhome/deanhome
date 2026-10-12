# Serato Analyzer

A desktop app for DJs who use Serato. It runs on **macOS and Windows** from one
Python + PySide6 codebase. This directory covers:

* **§20 Cross-platform support**: platform-specific modules, build scripts
  for a macOS `.app`/`.dmg` and a Windows `.exe`/installer, and CI that tests
  and builds on native runners.
* **§21 Smart Cover Art Finder**: finds missing, low-resolution or suspect
  artwork, searches MusicBrainz/Cover Art Archive and (optionally) Apple Music,
  lets you review every candidate, and applies approved artwork safely.

To integrate the finder, the app also includes a minimal **dashboard**, **folder
scanner**, **Serato crate browser**, **analysis queue** and **reports** page.
The audio-analysis features from the other sections of the spec are not part of
this directory.

## Quick start

```bash
cd serato-analyzer
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
serato-analyzer                 # or: python -m serato_analyzer
serato-analyzer-cli doctor      # folders, drives, Serato libraries, dependencies
```

Python 3.10+ is required. The dependencies are PySide6, mutagen and Pillow.

## Layout

```
src/serato_analyzer/
  platforms/        OS-specific code: app-data dirs, drives/volumes, Serato
    base.py         library discovery, crate-path resolution, permission hints,
    macos.py        dependency + architecture detection.
    windows.py      (linux.py exists for development/CI only)
    linux.py
  fsutil.py         Windows-safe filenames, traversal-safe joins, atomic replace
  config.py         settings (JSON in the platform config dir)
  db.py             SQLite schema shared by both platforms
  metadata.py       tag + embedded-artwork reading (mutagen)
  scanner.py        folder scanner
  serato/crates.py  read-only Serato crate parser
  jobs.py           background queue: pause / resume / cancel / retry
  reports.py        CSV / JSON reports
  artwork/
    providers/      musicbrainz.py, apple_music.py, manual.py (web search, URL, file)
    http.py         rate-limited, size-capped, http(s)-only client
    matching.py     query building, scoring, confidence
    images.py       content validation, cache, square normalisation
    embed.py        embedding + audio-payload/tag verification
    backups.py      verified backups and restore
    service.py      search / review / select orchestration
    apply.py        the three apply modes + change preview
  gui/              PySide6 interface
packaging/          PyInstaller spec, macOS + Windows build scripts, Inno Setup
tests/              pytest suite (no network, no audio samples needed)
```

Audio analysis, metadata, artwork discovery and the database schema are shared.
Only the modules under `platforms/` and the packaging scripts differ by OS.

## Where things are stored

| | macOS | Windows |
|---|---|---|
| Settings | `~/Library/Application Support/SeratoAnalyzer/settings.json` | `%APPDATA%\SeratoAnalyzer\settings.json` |
| Database, backups | `~/Library/Application Support/SeratoAnalyzer/` | `%LOCALAPPDATA%\SeratoAnalyzer\` |
| Artwork cache, temp downloads | `~/Library/Caches/SeratoAnalyzer/` | `%LOCALAPPDATA%\SeratoAnalyzer\Cache\` |
| Prepared tracks (default) | `~/Music/Serato_Analyzed_Music` | `<Music folder>\Serato_Analyzed_Music` |

The app does not assume your music is on the system drive. It lists every
mounted volume, such as `/Volumes/DJ Music/` or `D:\DJ Music\`. It also finds
`_Serato_` libraries both in the Music folder and at the root of each external
drive. Crate paths resolve relative to the volume that holds the library.

On **macOS**, choosing folders through the Open dialog grants access in the
normal way. If macOS still blocks a folder, the app explains which Privacy &
Security setting to change. On **Windows**, it flags Controlled Folder Access
and files locked by another program. Atomic replaces retry on brief locks.

## Artwork Finder

**Workflow:** scan a folder or crate, search in the background, review, approve,
preview changes, apply (to copies by default), verify, then export a report.
Searches and downloads run on the job queue, so the window never freezes. Jobs
can be paused, resumed, cancelled and retried.

### Providers

* **MusicBrainz + Cover Art Archive** (on by default). Searches by MusicBrainz
  release ID from tags if present, then by ISRC, then by artist + title. It
  stays under one request per second, sends a descriptive User-Agent, honours
  `Retry-After`, and caches results. Add a contact e-mail in Settings, as
  MusicBrainz asks.
* **Apple Music catalog** (optional). Needs a MusicKit **developer token** in
  Settings or `SERATO_ANALYZER_APPLE_MUSIC_TOKEN`. Without a token it reports
  "not configured" and other providers carry on. The storefront is
  configurable (default `us`).
* **Manual**. *Search Web for Cover Art* opens your browser with a query built
  from the track. You can then paste the URL of an image you're allowed to use,
  or import a file. Search engines are never scraped.

### Matching

Candidates are scored on artist, title, version/remix, album, year,
label/catalog number, ISRC and release ID, and get a **high / medium / low**
confidence:

* Artist alone, or title alone, is never enough: it's capped at low.
* A remix compared with the original release (or a different remix) is capped
  at medium.
* DJ-pool tags such as *Clean*, *Dirty*, *Intro* and *Extended* don't count as
  different versions.
* Tracks that look like DJ edits, bootlegs or mashups are capped at medium. If
  nothing is found, the app says such tracks often have no public cover and
  points you to manual selection.

### Safety rules (enforced in code and covered by tests)

* Nothing is applied without review. Auto-approval of high-confidence matches
  is **off** by default. Even when on, it only applies to tracks without
  artwork.
* Existing embedded artwork is replaced only after you mark the track for
  replacement and confirm.
* **Originals are never modified by default.** The default apply mode copies
  tracks to `Serato_Analyzed_Music`. Changing originals needs a Settings switch
  *and* a per-run confirmation, and makes a verified backup first. Backups can
  be restored from the Reports page.
* Every embed is written to a temporary copy first. The copy is then verified:
  the picture reads back, the **audio payload is byte-identical**, and other
  tags are unchanged. Only then does it atomically replace the destination.
  Audio is never re-encoded.
* Downloads come only from the provider's own image hosts or a URL you supply.
  They use http(s) only (redirects included), with a 15 MB cap by default.
  Images are validated by content (signature plus full decode), not by file
  extension. Only JPEG/PNG are accepted, with dimension limits. Cached files
  are named by SHA-256, so remote names never reach the filesystem.
* Artwork is normalised to a square of 1000 px by default, which is
  configurable. It is never stretched or enlarged. Non-square images get a crop
  chooser, or can be padded.
* Clearing the cache never touches music files, embedded artwork, or artwork
  saved with Mode 1.

### Apply modes

1. **Save artwork separately**: saves the image to the app's saved-artwork
   folder, and optionally beside the track. Audio files are unchanged.
2. **Embed**: embeds into copies, or into originals if allowed and confirmed.
   Supports MP3, AIFF, WAV, FLAC, M4A/ALAC, Ogg Vorbis and Opus.
3. **Prepare for Serato**: copies into `Serato_Analyzed_Music`, keeping the
   folder structure, and embeds and verifies the art. Formats that can't hold
   art are copied with a `.jpg` beside them. The app shows import steps for
   Serato afterwards.

Serato caches artwork. Changing embedded art does not refresh artwork already
shown for existing library entries or crates. The import instructions explain
this, and suggest *Rescan ID3 Tags* where needed.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

The suite builds MP3, WAV, AIFF, FLAC, M4A and Ogg files byte-by-byte and mocks
every HTTP response, so it needs no network, encoder or sample music. It
covers:

* macOS/Windows path, drive-letter and volume logic, using pure path flavours
  so both run on any host.
* Rate limiting, failed and invalid API responses, and offline operation.
* Corrupt, invalid and oversized images.
* Embedding and preservation of audio and tags for each format.
* Refusing to overwrite existing artwork.
* Backup and restore.
* All three apply modes.
* The job queue and crate parsing.
* An offscreen GUI run-through.

## Building

PyInstaller does not cross-compile. Build each target on that OS, and on that
CPU architecture for macOS. The workflow
`.github/workflows/serato-analyzer.yml` does this on native runners and uploads
the results.

**macOS** (`.app` + `.dmg`):

```bash
packaging/macos/build_macos.sh                         # native arch
TARGET_ARCH=x86_64 packaging/macos/build_macos.sh      # on an Intel Mac / runner
MAKE_DMG=0 ...                                         # skip the .dmg
CODESIGN_IDENTITY="Developer ID Application: …" ...    # sign (then notarise with notarytool)
```

Build Apple Silicon and Intel separately. A `universal2` build needs a
universal2 Python *and* universal2 wheels for every dependency. Without signing
and notarisation, Gatekeeper will warn on other Macs.

**Windows** (`.exe`, portable `.zip`, optional Inno Setup installer):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1 -Installer
```

The build's architecture (x64 or ARM64) follows the Python that runs the script.
The installer is per-user, and leaves settings and backups in place on
uninstall.

Both scripts run the built bundle with `--self-test`. This confirms the bundle
can load Qt, mutagen and Pillow on that machine.
