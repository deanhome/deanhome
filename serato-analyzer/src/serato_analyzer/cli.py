"""Command-line companion: diagnostics, scanning, artwork search and reports.

    serato-analyzer-cli doctor
    serato-analyzer-cli scan "/Volumes/DJ Music"
    serato-analyzer-cli find-artwork "D:\\DJ Music" --report artwork.csv

The CLI never applies artwork; review and apply in the app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .context import AppContext
from .reports import artwork_report, export_report
from .scanner import ScanErrors, iter_audio_files


def _scan(ctx: AppContext, folder: Path) -> list[int]:
    errors = ScanErrors()
    ids = []
    for path in iter_audio_files(folder, errors=errors, hint=ctx.platform.permission_hint):
        try:
            ids.append(ctx.scanner.scan_file(path, folder))
        except OSError as exc:
            print(f"  ! {path}: {exc}", file=sys.stderr)
    for p, hint in errors.permission_denied:
        print(f"  ! {hint}", file=sys.stderr)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serato-analyzer-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="show platform, folders, drives and dependencies")
    p_scan = sub.add_parser("scan", help="scan a music folder")
    p_scan.add_argument("folder", type=Path)
    p_find = sub.add_parser("find-artwork", help="scan a folder and search artwork for tracks that need it")
    p_find.add_argument("folder", type=Path)
    p_find.add_argument("--report", type=Path)
    p_find.add_argument("--force", action="store_true", help="ignore cached search results")
    args = parser.parse_args(argv)

    ctx = AppContext()
    try:
        if args.cmd == "doctor":
            pf = ctx.platform
            print(f"Platform:   {pf.name}")
            print(f"Settings:   {pf.settings_path()}")
            print(f"Database:   {pf.database_path()}")
            print(f"Cache:      {pf.artwork_cache_dir()}")
            print(f"Output:     {ctx.applier.output_root()}")
            print("Volumes:")
            for v in pf.list_volumes():
                flags = ", ".join(f for f, on in (("system", v.is_system), ("removable", v.is_removable),
                                                   ("network", v.is_network)) if on)
                print(f"  {v.root}  {v.label}  {flags}")
            print("Serato libraries:")
            for lib in pf.discover_serato_libraries():
                print(f"  {lib.root}")
            print("Dependencies:")
            for d in pf.detect_dependencies():
                mark = "ok " if d.available else ("MISSING" if d.required else "-- ")
                print(f"  [{mark}] {d.name} {d.version or ''} {d.detail}")
            for name, problem in ctx.artwork.provider_overview():
                print(f"Provider {name}: {problem or 'ready'}")
            return 0
        ids = _scan(ctx, args.folder)
        print(f"Scanned {len(ids)} tracks.")
        if args.cmd == "find-artwork":
            todo = ctx.artwork.tracks_needing_artwork(ids)
            print(f"{len(todo)} need artwork.")
            for n, tid in enumerate(todo, 1):
                s = ctx.artwork.search_track(tid, force=args.force)
                row = ctx.db.get_track(tid)
                print(f"[{n}/{len(todo)}] {row['artist']} - {row['title']}: {s.status.value} {s.message}")
            if args.report:
                export_report(artwork_report(ctx.db, ids), args.report)
                print(f"Report written to {args.report}")
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
