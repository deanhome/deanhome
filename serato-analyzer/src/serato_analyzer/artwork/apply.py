"""Apply approved artwork: save separately, embed, or prepare copies for Serato.

Defaults are conservative:

* Mode 3 (prepare copies for Serato) is the default; originals are untouched.
* Modifying an original file needs ``allow_modify_originals`` in Settings *and*
  ``confirmed=True`` for the call, and a verified backup is made first.
* Replacing existing embedded artwork needs the track to be explicitly marked
  for replacement by the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..db import Database
from ..fsutil import atomic_write_bytes, copy_verified, safe_filename, safe_join, sha256_bytes, unique_path
from ..platforms import PlatformServices
from .backups import BackupManager
from .embed import EmbedError, safe_embed, supports_embedding
from .models import ApplyMode, ReviewStatus

SERATO_IMPORT_INSTRUCTIONS = """\
Importing the prepared tracks into Serato

1. Open Serato DJ.
2. Open the Files panel (or Finder / File Explorer) and locate the
   "Serato_Analyzed_Music" folder.
3. Drag the folder, or the tracks you want, onto your Library or onto a crate.
   Serato adds them as new library entries that point at these copies.
4. If a prepared copy was already in your library, select it and use Serato's
   "Rescan ID3 Tags" option (in the Files panel or the track's context menu,
   depending on your Serato version) so it re-reads the embedded artwork.

Notes
* Your original files were not changed. Crates that already contain the
  originals still point to the originals, so they will not show the new artwork
  until you add the prepared copies to those crates.
* Serato caches artwork. Changing embedded artwork does not automatically update
  artwork already shown for existing library entries or crates.
* Formats that cannot hold embedded artwork were copied unchanged, with the
  artwork saved as a .jpg beside the copy.
"""


@dataclass
class PlannedChange:
    track_id: int
    mode: ApplyMode
    source: Path
    destination: Path | None
    action: str
    image_path: Path | None = None
    image_size: str = ""
    replaces_existing_artwork: bool = False
    modifies_original: bool = False
    extra_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


@dataclass
class ApplyOutcome:
    track_id: int
    ok: bool
    status: str
    destination: Path | None = None
    message: str = ""
    backup_path: Path | None = None


class ArtworkApplier:
    def __init__(self, db: Database, settings: Settings, platform: PlatformServices,
                 backups: BackupManager, saved_artwork_dir: Path) -> None:
        self.db = db
        self.settings = settings
        self.platform = platform
        self.backups = backups
        self.saved_artwork_dir = Path(saved_artwork_dir)

    def output_root(self) -> Path:
        return Path(self.settings.output_dir or self.platform.default_output_dir())

    # ------------------------------------------------------------------
    def _copy_destination(self, row, root: Path) -> Path:
        source = Path(row["path"])
        rel_parts: list[str] = [source.name]
        if row["scan_root"]:
            try:
                rel_parts = list(source.relative_to(Path(row["scan_root"])).parts)
            except ValueError:
                pass
        dest = safe_join(root, *rel_parts)
        if dest.exists() and not self._is_previous_output(row, dest):
            dest = unique_path(dest)
        return dest

    def _is_previous_output(self, row, dest: Path) -> bool:
        return bool(row["applied_path"]) and Path(row["applied_path"]) == dest

    def plan(self, track_ids: list[int], mode: ApplyMode, in_place: bool = False) -> list[PlannedChange]:
        plans: list[PlannedChange] = []
        for row in self.db.artwork_rows(track_ids):
            source = Path(row["path"])
            image_path = Path(row["selected_image_path"]) if row["selected_image_path"] else None
            plan = PlannedChange(row["id"], mode, source, None, "", image_path)
            plans.append(plan)
            if row["review_status"] != ReviewStatus.APPROVED.value or image_path is None:
                plan.blocked_reason = "No approved artwork for this track."
                continue
            if not image_path.is_file():
                plan.blocked_reason = "The selected image is no longer in the cache; select it again."
                continue
            if not source.is_file():
                plan.blocked_reason = "The audio file is missing or its drive is not connected."
                continue
            plan.image_size = _image_size_text(image_path)
            has_art = bool(row["has_embedded"])

            if mode == ApplyMode.SAVE_SEPARATELY:
                plan.destination = self.saved_artwork_dir / f"{row['id']}_{safe_filename(source.stem, max_bytes=120)}.jpg"
                plan.action = "Save the artwork image to the artwork folder (audio file not changed)"
                if self.settings.save_artwork_alongside_music:
                    beside = source.with_name(safe_filename(source.stem) + ".jpg")
                    if beside.exists():
                        beside = unique_path(beside)
                        plan.warnings.append("An image with the same name exists beside the track; "
                                             f"saving as {beside.name}.")
                    plan.extra_files.append(beside)
                continue

            if has_art:
                if not row["replace_requested"]:
                    plan.blocked_reason = ("The track already has embedded artwork. Mark it for "
                                           "replacement first to confirm you want to replace it.")
                    continue
                plan.replaces_existing_artwork = True
                plan.warnings.append(f"Replaces existing embedded artwork "
                                     f"({row['embedded_width']}×{row['embedded_height']}).")

            embeddable = supports_embedding(source)
            if mode == ApplyMode.EMBED:
                if not embeddable:
                    plan.blocked_reason = (f"{source.suffix or 'This'} files cannot hold embedded artwork; "
                                           "use 'Save artwork separately'.")
                    continue
                if in_place:
                    if not self.settings.allow_modify_originals:
                        plan.blocked_reason = ("Modifying original files is turned off in Settings. "
                                               "Apply to copies instead.")
                        continue
                    plan.destination = source
                    plan.modifies_original = True
                    plan.action = "Embed artwork into the ORIGINAL file (a verified backup is made first)"
                else:
                    plan.destination = self._copy_destination(row, self.output_root())
                    plan.action = "Embed artwork into a copy"
            else:  # PREPARE_FOR_SERATO
                plan.destination = self._copy_destination(row, self.output_root())
                if embeddable:
                    plan.action = "Copy to Serato_Analyzed_Music and embed artwork"
                else:
                    plan.action = "Copy to Serato_Analyzed_Music (format cannot embed; artwork saved beside it)"
                    plan.extra_files.append(plan.destination.with_suffix(".jpg"))
                    plan.warnings.append(f"{source.suffix} cannot hold embedded artwork.")
            if plan.destination and plan.destination.exists() and not plan.modifies_original:
                plan.warnings.append("Updates a previously prepared copy (it is backed up first).")
        return plans

    # ------------------------------------------------------------------
    def apply(self, track_ids: list[int], mode: ApplyMode, *, in_place: bool = False,
              confirmed: bool = False,
              progress: Callable[[int, int, ApplyOutcome], None] | None = None,
              should_stop: Callable[[], bool] | None = None) -> list[ApplyOutcome]:
        plans = self.plan(track_ids, mode, in_place)
        if in_place and not confirmed and any(p.modifies_original for p in plans):
            raise PermissionError("Modifying original files requires explicit confirmation.")
        outcomes: list[ApplyOutcome] = []
        for i, plan in enumerate(plans, 1):
            if should_stop and should_stop():
                break
            outcome = self._apply_one(plan)
            outcomes.append(outcome)
            if progress:
                progress(i, len(plans), outcome)
        return outcomes

    def _apply_one(self, plan: PlannedChange) -> ApplyOutcome:
        tid = plan.track_id
        if plan.blocked:
            return ApplyOutcome(tid, False, "skipped", None, plan.blocked_reason or "")
        state = self.db.get_artwork_state(tid)
        image = plan.image_path.read_bytes()
        if sha256_bytes(image) != state["selected_image_hash"]:
            msg = "The cached artwork changed since it was approved; select it again."
            self.db.update_artwork_state(tid, embedding_status="failed", last_error=msg)
            return ApplyOutcome(tid, False, "failed", None, msg)
        try:
            if plan.mode == ApplyMode.SAVE_SEPARATELY:
                plan.destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(plan.destination, image)
                for extra in plan.extra_files:
                    atomic_write_bytes(extra, image)
                self._record(tid, plan, "not_embedded", None)
                return ApplyOutcome(tid, True, "saved", plan.destination, "Artwork saved; audio unchanged.")

            if plan.mode == ApplyMode.PREPARE_FOR_SERATO and not supports_embedding(plan.source):
                if plan.destination.exists():
                    self.backups.backup(plan.destination, tid, "re-prepare copy")
                copy_verified(plan.source, plan.destination)
                for extra in plan.extra_files:
                    atomic_write_bytes(extra, image)
                self._record(tid, plan, "unsupported_format", None)
                return ApplyOutcome(tid, True, "copied", plan.destination,
                                    "Copied; this format cannot hold embedded artwork.")

            result = safe_embed(plan.source, plan.destination, image,
                                replace_existing=plan.replaces_existing_artwork,
                                backups=self.backups, track_id=tid)
            self._record(tid, plan, "verified", result.backup_path)
            msg = "Artwork embedded and verified."
            if result.backup_path:
                msg += f" Backup: {result.backup_path}"
            return ApplyOutcome(tid, True, "embedded", plan.destination, msg, result.backup_path)
        except (EmbedError, OSError) as exc:
            self.db.update_artwork_state(tid, embedding_status="failed", last_error=str(exc))
            return ApplyOutcome(tid, False, "failed", plan.destination, str(exc))

    def _record(self, tid: int, plan: PlannedChange, embedding_status: str, backup: Path | None) -> None:
        state = self.db.get_artwork_state(tid)
        values = dict(embedding_status=embedding_status, apply_mode=plan.mode.value,
                      applied_path=str(plan.destination), applied_image_hash=state["selected_image_hash"],
                      review_status=ReviewStatus.APPLIED.value, last_error=None)
        if backup:
            values["backup_path"] = str(backup)
        if plan.modifies_original:
            # The library file itself now carries the new artwork.
            values.update(has_embedded=1, embedded_hash=state["selected_image_hash"],
                          artwork_status="ok", replace_requested=0)
        self.db.update_artwork_state(tid, **values)


def _image_size_text(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return f"{im.size[0]} × {im.size[1]}"
    except Exception:
        return ""
