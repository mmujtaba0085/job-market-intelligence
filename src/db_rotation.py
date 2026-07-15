"""
src/db_rotation.py
────────────────────
Merges Buffer into Free, flips the Serving pointer, then refreshes the
newly-demoted file - see
docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md.

rotate() is the only public entry point. Two callers: src/orchestrator.py
(right after an ingest-only run's finish_run() succeeds - no site-traffic
awareness needed there, so it calls rotate() with no arguments) and
web_viewer.py's _auto_scheduler_loop 60s fallback tick (which DOES track
site traffic via _last_request_at, and passes it through so rotate() can
defer to should_process_chunk() the same way the classification scheduler
already does - this is the "still cares about not fighting an admin doing
manual tagging mid-merge" case from the spec's Classification pipeline
changes section).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date as _date, datetime, timezone
from pathlib import Path

from src.classification.scheduling import should_process_chunk
from src.storage import db
from src.storage.models import JobNormalized, SkillSignal

logger = logging.getLogger(__name__)


def rotate(last_request_at: "datetime | None" = None, now: "datetime | None" = None) -> dict:
    if last_request_at is not None or now is not None:
        check_now = now or datetime.now(timezone.utc)
        if not should_process_chunk(last_request_at, other_run_active=False, now=check_now):
            logger.info("[db_rotation] Skipped: site busy")
            return {"merged": 0, "rotated": False, "new_serving": db._read_pointer()}

    if db.fcntl is None:
        return _rotate_impl()

    db._ROTATION_LOCK_PATH.touch(exist_ok=True)
    with open(db._ROTATION_LOCK_PATH, "r+") as lock_file:
        db.fcntl.flock(lock_file, db.fcntl.LOCK_EX)
        try:
            return _rotate_impl()
        finally:
            db.fcntl.flock(lock_file, db.fcntl.LOCK_UN)


def _rotate_impl() -> dict:
    merged = _merge_buffer_into_free()

    which_before = db._read_pointer()
    which_after = "b" if which_before == "a" else "a"
    db._write_pointer(which_after)

    demoted_path = db._serving_path_for(which_before)   # was Serving, now demoted
    new_serving_path = db._serving_path_for(which_after)
    _refresh_demoted_file(source=new_serving_path, destination=demoted_path)

    from src.pipeline_monitor import set_config
    set_config("last_rotation_at", datetime.now(timezone.utc).isoformat())

    logger.info(
        "[db_rotation] Rotated %s -> %s, merged %d buffered job(s)",
        which_before, which_after, merged,
    )
    return {"merged": merged, "rotated": True, "new_serving": which_after}


def _merge_buffer_into_free() -> int:
    """Copies Buffer's jobs (+ their skills and multi-location entries) into
    Free, skipping anything Free already has by url_hash - reuses
    db.upsert_job()'s existing dedup check rather than reimplementing it,
    per the spec."""
    buffer_conn = db.get_buffer_connection()
    try:
        buffer_jobs = buffer_conn.execute("SELECT * FROM jobs").fetchall()
        skills_by_job_id = {}
        locations_by_job_id = {}
        for job_row in buffer_jobs:
            job_id = job_row["job_id"]
            skills_by_job_id[job_id] = buffer_conn.execute(
                "SELECT * FROM skills WHERE job_id = ?", (job_id,)
            ).fetchall()
            location_rows = buffer_conn.execute(
                "SELECT DISTINCT location FROM job_locations WHERE job_id = ?", (job_id,)
            ).fetchall()
            if location_rows:
                locations_by_job_id[job_id] = [r["location"] for r in location_rows]
    finally:
        buffer_conn.close()

    merged = 0
    with db.use_free_connection():
        for job_row in buffer_jobs:
            job = _row_to_job_normalized(
                job_row, all_locations=locations_by_job_id.get(job_row["job_id"])
            )
            free_job_id, status = db.upsert_job(job)
            if status != "inserted":
                continue
            merged += 1
            signals = [
                SkillSignal(
                    job_id=free_job_id, market_id=s["market_id"],
                    raw_detected_skill=s["raw_detected_skill"], normalized_skill=s["normalized_skill"],
                    category=s["category"], confidence_score=s["confidence_score"],
                    extraction_method=s["method"],
                )
                for s in skills_by_job_id.get(job_row["job_id"], [])
            ]
            if signals:
                db.insert_skills(signals)

    buffer_conn = db.get_buffer_connection()
    try:
        with buffer_conn:
            buffer_conn.execute("DELETE FROM skills")
            buffer_conn.execute("DELETE FROM jobs")
            buffer_conn.execute("DELETE FROM job_locations")
    finally:
        buffer_conn.close()

    return merged


def _row_to_job_normalized(row: sqlite3.Row, all_locations: "list[str] | None" = None) -> JobNormalized:
    """Reconstructs a JobNormalized from a raw `jobs` table row. Field names
    below are verified against src/storage/models.py::JobNormalized and the
    real `jobs` table columns (001_init.sql + the PRAGMA-conditional column
    additions in db.py::_run_rotating_migrations_impl()) - not just the
    subset of fields a first pass at this reconstruction would assume.
    Notably includes salary_period/newspaper/ad_image_url/apply_url (all
    real jobs columns added by migrations 011-013) and all_locations
    (reconstructed from the buffer's job_locations rows, passed in by the
    caller) so multi-location jobs don't silently lose locations on merge.
    Fields on JobNormalized with no backing jobs column at all (source_id,
    source_record_id, listing_status, salary_raw, salary_is_estimated,
    structured_locations - none of these are written by db.upsert_job()
    either) are left at their dataclass defaults."""
    posted_date = None
    if row["posted_date"]:
        posted_date = _date.fromisoformat(row["posted_date"])

    return JobNormalized(
        url_hash=row["url_hash"], canonical_hash=row["canonical_hash"],
        description_hash=row["description_hash"], job_group_id=row["job_group_id"],
        market_id=row["market_id"], source_name=row["source_name"],
        title=row["title"], normalized_title=row["normalized_title"] or row["title"],
        normalization_confidence=row["normalization_confidence"] or 0.0,
        company=row["company"], country=row["country"], location=row["location"],
        remote_type=row["remote_type"], posted_date=posted_date,
        salary_min=row["salary_min"], salary_max=row["salary_max"], currency=row["currency"],
        description_text=row["raw_description"] or "", url=row["url"],
        all_locations=all_locations,
        newspaper=row["newspaper"], ad_image_url=row["ad_image_url"], apply_url=row["apply_url"],
        salary_period=row["salary_period"],
    )


def _refresh_demoted_file(source: Path, destination: Path) -> None:
    """Backs up `source` (the just-updated new-Serving file) into a temp
    file via db._sqlite_file_backup() - the same Online Backup API call
    shape already used by db.py's own bootstrap and by
    scripts/warehouse_rollout.py::_sqlite_backup() - then os.replace()s it
    over the demoted file's path. Atomic rename: any request that already
    opened the demoted file (read the pointer a moment before the flip)
    keeps reading its own consistent snapshot until it closes; nothing
    blocks, nothing errors. This is deliberately NOT a lock and NOT an
    in-place overwrite - see the spec's Safety section for why.

    All connections in this app run in WAL mode (db.py's _connect()), and
    `destination` was Serving until moments ago - it realistically has its
    own non-empty -wal/-shm sidecars from live traffic. os.replace() only
    swaps the main file; a stale -wal left next to the fresh one would get
    replayed by the next connection opened against `destination`, silently
    reverting it back toward pre-rotation content (PRAGMA integrity_check
    still reports "ok" - this fails silently, not with an error). The
    backup itself doesn't have this problem (sqlite3.Connection.backup()
    operates through a live connection to `source`, which already reflects
    any of its own WAL content), so only destination's leftover sidecars
    need cleaning up, not source's."""
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    db._sqlite_file_backup(source, tmp_path)
    os.replace(tmp_path, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = destination.with_name(destination.name + suffix)
        try:
            os.remove(sidecar)
        except FileNotFoundError:
            pass
