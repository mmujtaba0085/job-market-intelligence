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
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS_THRESHOLD = 300
DEFAULT_LOCAL_CHUNK_SIZE = 500
DEFAULT_GROQ_CHUNK_SIZE = 25
DEFAULT_RETRY_INTERVAL_SECONDS = 3600


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


def run_scheduler_tick(conn, last_request_at: datetime | None, now: datetime) -> None:
    from src.classification.groq_stage import process_groq_queue
    from src.classification.local_stage import classify_pending_jobs
    from src.pipeline_monitor import get_config

    # Read once per tick - admin-configurable via /admin/classification's
    # config form (classification_idle_seconds / _local_chunk_size / _groq_chunk_size),
    # falling back to the module defaults if unset.
    cfg = get_config()
    idle_threshold = int(cfg.get("classification_idle_seconds", DEFAULT_IDLE_SECONDS_THRESHOLD))
    local_chunk_size = int(cfg.get("classification_local_chunk_size", DEFAULT_LOCAL_CHUNK_SIZE))
    groq_chunk_size = int(cfg.get("classification_groq_chunk_size", DEFAULT_GROQ_CHUNK_SIZE))

    # local_incremental: always-on, never load-gated (small volume, cheap).
    if _has_pending_local_work(conn) and not _any_run_active(conn, "local_incremental"):
        run_id = _start_run(conn, "local_incremental", trigger="schedule")
        try:
            classify_pending_jobs(conn, run_id=run_id)
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] local_incremental failed: %s", exc)
            _finish_run(conn, run_id, status="failed")

    # groq_backlog: auto-starts on idle, chunked, load-gated. local_full_backfill's
    # active status can't change between the start-check and the continuation
    # below (nothing in between writes to it), so it's read once and reused
    # rather than re-queried.
    other_active = _any_run_active(conn, "local_full_backfill")
    if _has_pending_groq_backlog(conn) and not _any_run_active(conn, "groq_backlog"):
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            _start_run(conn, "groq_backlog", trigger="backfill_idle")
            # Falls through to the continuation branch below on this same tick.

    if _any_run_active(conn, "groq_backlog"):
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type = 'groq_backlog' AND status = 'running' LIMIT 1").fetchone()
            run_id = run["run_id"]
            process_groq_queue(conn, run_id=run_id, statuses=("pending",), limit=groq_chunk_size)
            if not _has_pending_groq_backlog(conn):
                _finish_run(conn, run_id, status="success")

    # local_full_backfill: manual-start only (admin action creates the 'running'
    # row elsewhere); this tick only ever CONTINUES an already-started one.
    if _any_run_active(conn, "local_full_backfill"):
        other_active = _any_run_active(conn, "groq_backlog")
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            from src.classification.local_stage import reclassify_all
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
    # to local_full_backfill and groq_backlog) - this is a small-volume, purely
    # time-based cadence, independent of site traffic.
    if _groq_retry_due(conn, now) and not _any_run_active(conn, "groq_retry"):
        run_id = _start_run(conn, "groq_retry", trigger="schedule")
        try:
            process_groq_queue(conn, run_id=run_id, statuses=("failed_technical",))
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] groq_retry failed: %s", exc)
            _finish_run(conn, run_id, status="failed")
