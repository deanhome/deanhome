"""Artwork report export (CSV / JSON)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .db import Database, utcnow
from .fsutil import atomic_write_bytes

COLUMNS = ["outcome", "artist", "title", "album", "path", "artwork_status", "search_status",
           "review_status", "provider", "confidence", "apply_mode", "applied_path",
           "embedding_status", "backup_path", "last_error"]


def classify(row) -> str:
    if row["review_status"] == "applied":
        return "completed"
    if row["last_error"] or "failed" in (row["search_status"], row["download_status"], row["embedding_status"]):
        return "failed"
    if row["review_status"] in ("skipped", "rejected", "no_artwork_available"):
        return "skipped"
    return "pending"


def artwork_report(db: Database, track_ids: list[int] | None = None) -> dict:
    rows = db.artwork_rows(track_ids)
    cand_rows = {r["id"]: r for r in db.query("SELECT id, provider, confidence FROM artwork_candidates")}
    items = []
    for r in rows:
        cand = cand_rows.get(r["selected_candidate_id"])
        items.append({
            "outcome": classify(r), "artist": r["artist"] or "", "title": r["title"] or "",
            "album": r["album"] or "", "path": r["path"], "artwork_status": r["artwork_status"],
            "search_status": r["search_status"], "review_status": r["review_status"],
            "provider": cand["provider"] if cand else "", "confidence": cand["confidence"] if cand else "",
            "apply_mode": r["apply_mode"] or "", "applied_path": r["applied_path"] or "",
            "embedding_status": r["embedding_status"], "backup_path": r["backup_path"] or "",
            "last_error": r["last_error"] or "",
        })
    summary = {k: sum(1 for i in items if i["outcome"] == k) for k in ("completed", "skipped", "failed", "pending")}
    return {"generated_at": utcnow(), "summary": summary, "tracks": items}


def export_report(report: dict, path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() == ".json":
        atomic_write_bytes(path, json.dumps(report, indent=2).encode("utf-8"))
        return path
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(report["tracks"])
    # UTF-8 with BOM so Excel on Windows opens accented names correctly.
    atomic_write_bytes(path, buf.getvalue().encode("utf-8-sig"))
    return path
