"""
Load-aware scheduling decisions for the classification pipeline. The
"should I do work right now" check is a pure function (no I/O, no sleep)
so it's testable without real time passing - mirroring how
src.pipeline_monitor.compute_next_run() is separated from
_auto_scheduler_loop's actual sleep in web_viewer.py.

run_scheduler_tick() is the per-tick orchestrator called from
_auto_scheduler_loop; it is NOT itself a subprocess launcher (unlike
pipeline_monitor.launch_pipeline) - classification chunks run in-process,
since they're small and frequent, not long isolated jobs like ingest/crawl.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS_THRESHOLD = 300
DEFAULT_LOCAL_CHUNK_SIZE = 500
DEFAULT_GROQ_CHUNK_SIZE = 25
DEFAULT_RETRY_INTERVAL_SECONDS = 3600
# A 'running' classification_runs row whose process died (crash, OOM,
# container redeploy) without ever reaching _finish_run() would otherwise
# block every future run of the same run_type forever - _any_run_active()
# has no way to distinguish a genuinely in-progress run from an abandoned
# one. Confirmed in production: two runs raced to start within the same
# millisecond, both stuck at 0 progress, and nothing ran for the ~24h
# after that. 30 minutes is generously above the ~3 minutes a real
# 5000-job local_incremental chunk has taken in production.
STALE_RUN_THRESHOLD_SECONDS = 1800


def should_process_chunk(
    last_request_at: datetime | None,
    other_run_active: bool,
    now: datetime,
    idle_seconds_threshold: int = DEFAULT_IDLE_SECONDS_THRESHOLD,
) -> bool:
    if other_run_active:
        return False
    if last_request_at is None:
        return True
    idle_seconds = (now - last_request_at).total_seconds()
    return idle_seconds >= idle_seconds_threshold


def _any_run_active(conn, run_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM classification_runs WHERE run_type = ? AND status = 'running' LIMIT 1", (run_type,)
    ).fetchone()
    return row is not None


def _start_run(conn, run_type: str, trigger: str) -> str:
    run_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, ?, ?, 'running', ?)",
        (run_id, run_type, trigger, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return run_id


def _finish_run(conn, run_id: str, status: str = "success") -> None:
    conn.execute(
        "UPDATE classification_runs SET status = ?, finished_at = ? WHERE run_id = ?",
        (status, datetime.now(timezone.utc).isoformat(), run_id),
    )
    conn.commit()


def _has_pending_local_work(conn) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE field_classification_attempted_at IS NULL LIMIT 1").fetchone()
    return row is not None


def _has_pending_groq_backlog(conn) -> bool:
    row = conn.execute("SELECT 1 FROM groq_classification_queue WHERE status = 'pending' LIMIT 1").fetchone()
    return row is not None


def _groq_retry_due(conn, now: datetime) -> bool:
    """True if no groq_retry run has ever started, or the last one started
    over an hour ago. Not load-gated (per Global Constraints) - this is a
    time-based cadence check only, independent of should_process_chunk()."""
    row = conn.execute(
        "SELECT started_at FROM classification_runs WHERE run_type = 'groq_retry' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return True
    last_started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    if last_started.tzinfo is None:
        last_started = last_started.replace(tzinfo=timezone.utc)
    return (now - last_started).total_seconds() >= DEFAULT_RETRY_INTERVAL_SECONDS


def _mark_stale_runs_failed(conn, now: datetime) -> None:
    """
    A raw string comparison against started_at doesn't work here: rows
    inserted via SQL's datetime('now') are space-separated with no
    timezone ('2026-07-16 03:15:22'), while this module's own writes use
    Python's timezone-aware .isoformat() ('2026-07-16T03:15:22+00:00') -
    the two formats don't sort consistently against each other as strings
    (space sorts below 'T', so a datetime('now') row would always look
    "older" than any Python-isoformat cutoff regardless of its real time).
    Parse each candidate properly instead, same approach _groq_retry_due()
    above already uses for the same started_at column.
    """
    stale_ids = []
    for row in conn.execute("SELECT run_id, started_at FROM classification_runs WHERE status = 'running'").fetchall():
        started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00").replace(" ", "T"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if (now - started).total_seconds() >= STALE_RUN_THRESHOLD_SECONDS:
            stale_ids.append(row["run_id"])

    for run_id in stale_ids:
        conn.execute(
            "UPDATE classification_runs SET status = 'failed', finished_at = ? WHERE run_id = ?",
            (now.isoformat(), run_id),
        )
    if stale_ids:
        conn.commit()


def run_scheduler_tick(conn, last_request_at: datetime | None, now: datetime) -> None:
    """
    Public entry point - wraps _run_scheduler_tick_impl() in a cross-process
    file lock (fcntl, Unix only - a no-op on Windows, same pattern already
    used for db.run_migrations()) so gunicorn's N independently-started
    scheduler threads (one per worker process - see web_viewer.py's
    module-level `_scheduler_thread = Thread(...)`) can never race each
    other into starting the same classification_runs row twice.
    """
    from src.storage import db

    if db.fcntl is None:
        _run_scheduler_tick_impl(conn, last_request_at, now)
        return

    db._CLASSIFICATION_SCHEDULER_LOCK_PATH.touch(exist_ok=True)
    with open(db._CLASSIFICATION_SCHEDULER_LOCK_PATH, "r+") as lock_file:
        db.fcntl.flock(lock_file, db.fcntl.LOCK_EX)
        try:
            _run_scheduler_tick_impl(conn, last_request_at, now)
        finally:
            db.fcntl.flock(lock_file, db.fcntl.LOCK_UN)


def _run_scheduler_tick_impl(conn, last_request_at: datetime | None, now: datetime) -> None:
    from src.classification.groq_stage import process_groq_queue
    from src.classification.local_stage import classify_pending_jobs, reclassify_all
    from src.pipeline_monitor import get_config

    # Second line of defense beyond the lock above: a run that slipped past
    # it and then never finished (crashed mid-chunk, killed by a redeploy)
    # must not block every future tick forever. See STALE_RUN_THRESHOLD_SECONDS.
    _mark_stale_runs_failed(conn, now)

    # Read once per tick - admin-configurable via /admin/classification's
    # config form (classification_local_chunk_size / _groq_chunk_size),
    # falling back to the module defaults if unset. classification_idle_seconds
    # is no longer read here - groq_backlog/local_full_backfill dropped their
    # idle-gating (Free is never serving live traffic, so there's nothing left
    # to protect it from; see spec's Classification pipeline changes section).
    cfg = get_config()
    local_chunk_size = int(cfg.get("classification_local_chunk_size", DEFAULT_LOCAL_CHUNK_SIZE))
    groq_chunk_size = int(cfg.get("classification_groq_chunk_size", DEFAULT_GROQ_CHUNK_SIZE))

    # local_incremental: always-on, never load-gated. Capped to local_chunk_size
    # per tick, NOT unbounded - on a fresh deploy every existing job has
    # field_classification_attempted_at IS NULL, so an uncapped call here would
    # be one single-transaction pass over the entire ~110k-job backlog (~68min
    # measured cost), holding SQLite's single-writer lock the whole time and
    # blocking every other write site-wide (ingestion, click tracking, admin
    # actions). Capping it needs no cursor/continuation logic the way
    # local_full_backfill does: field_classification_attempted_at IS NULL is
    # itself the natural "what's left" filter, so each tick's chunk is simply
    # its own complete, independent local_incremental run, and the backlog
    # drains gradually, one chunk per 60s tick, without ever locking for long.
    if _has_pending_local_work(conn) and not _any_run_active(conn, "local_incremental"):
        run_id = _start_run(conn, "local_incremental", trigger="schedule")
        try:
            classify_pending_jobs(conn, run_id=run_id, limit=local_chunk_size)
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] local_incremental failed: %s", exc)
            _finish_run(conn, run_id, status="failed")

    # groq_backlog: auto-starts when there's a backlog, chunked. No longer
    # load-gated - Free is never serving live traffic, so there's nothing to
    # protect it from (see spec's Classification pipeline changes section).
    if _has_pending_groq_backlog(conn) and not _any_run_active(conn, "groq_backlog"):
        _start_run(conn, "groq_backlog", trigger="backfill_idle")
        # Falls through to the continuation branch below on this same tick.

    if _any_run_active(conn, "groq_backlog"):
        run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type = 'groq_backlog' AND status = 'running' LIMIT 1").fetchone()
        run_id = run["run_id"]
        process_groq_queue(conn, run_id=run_id, statuses=("pending",), limit=groq_chunk_size)
        if not _has_pending_groq_backlog(conn):
            _finish_run(conn, run_id, status="success")

    # local_full_backfill: manual-start only (admin action creates the 'running'
    # row elsewhere); this tick only ever CONTINUES an already-started one.
    # No longer load-gated, same reasoning as groq_backlog above.
    if _any_run_active(conn, "local_full_backfill"):
        run = conn.execute("SELECT run_id, cursor_job_id FROM classification_runs WHERE run_type = 'local_full_backfill' AND status = 'running' LIMIT 1").fetchone()
        run_id = run["run_id"]
        cursor_job_id = run["cursor_job_id"]
        remaining = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_id > ?", (cursor_job_id or 0,)).fetchone()[0]
        # after_job_id is required here - without it, reclassify_all's
        # ORDER BY job_id LIMIT n query would deterministically re-select
        # the same first chunk on every tick and the run would never
        # advance past it (see Task 2's after_job_id addition).
        reclassify_all(conn, run_id=run_id, limit=local_chunk_size, after_job_id=cursor_job_id)
        if remaining <= local_chunk_size:
            _finish_run(conn, run_id, status="success")

    # groq_retry: hourly sweep of failed_technical rows under the attempt cap.
    # Deliberately NOT load-gated (Global Constraints: load gating applies only
    # to local_full_backfill and groq_backlog) - this is a time-based cadence,
    # independent of site traffic. MUST pass limit=groq_chunk_size the same way
    # groq_backlog's call below does: process_groq_queue() defaults to
    # limit=None (unbounded) unless a caller passes one explicitly, and this
    # call site didn't - confirmed in production, a large failed_technical
    # queue (this is not actually "small-volume" in practice) held the
    # cross-process scheduler lock for over an hour with zero progress
    # reported, blocking every other classification run entirely.
    if _groq_retry_due(conn, now) and not _any_run_active(conn, "groq_retry"):
        run_id = _start_run(conn, "groq_retry", trigger="schedule")
        try:
            process_groq_queue(conn, run_id=run_id, statuses=("failed_technical",), limit=groq_chunk_size)
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] groq_retry failed: %s", exc)
            _finish_run(conn, run_id, status="failed")
