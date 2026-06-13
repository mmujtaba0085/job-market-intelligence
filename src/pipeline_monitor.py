"""
src/pipeline_monitor.py
────────────────────────
Record pipeline run history, read schedule config, and launch pipeline runs
as subprocesses so the web server stays responsive.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime, timezone

from src.storage.db import get_connection

# ── Run recording ─────────────────────────────────────────────────────────────

def start_run(mode: str, trigger: str = "schedule") -> str:
    run_id = str(uuid.uuid4())[:8]
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """INSERT INTO pipeline_runs (run_id, mode, status, trigger, started_at)
                   VALUES (?, ?, 'running', ?, datetime('now'))""",
                (run_id, mode, trigger),
            )
    finally:
        conn.close()
    return run_id


def finish_run(
    run_id: str,
    *,
    status: str = "success",
    jobs_fetched: int = 0,
    jobs_inserted: int = 0,
    jobs_deduped: int = 0,
    skills_extracted: int = 0,
    error: str | None = None,
) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """UPDATE pipeline_runs SET
                       status           = ?,
                       finished_at      = datetime('now'),
                       duration_seconds = CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER),
                       jobs_fetched     = ?,
                       jobs_inserted    = ?,
                       jobs_deduped     = ?,
                       skills_extracted = ?,
                       error            = ?
                   WHERE run_id = ?""",
                (status, jobs_fetched, jobs_inserted, jobs_deduped, skills_extracted, error, run_id),
            )
    finally:
        conn.close()


def _cleanup_stale_runs(conn, timeout_minutes: int = 120) -> None:
    """Mark runs still 'running' after timeout_minutes as failed."""
    conn.execute(
        """UPDATE pipeline_runs
           SET status            = 'failed',
               finished_at       = datetime('now'),
               duration_seconds  = CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER),
               error             = 'Process terminated unexpectedly (stale run cleanup)'
           WHERE status = 'running'
             AND started_at < datetime('now', ?)""",
        (f"-{timeout_minutes} minutes",),
    )


def get_recent_runs(limit: int = 30) -> list[dict]:
    conn = get_connection()
    with conn:
        _cleanup_stale_runs(conn)
    rows = conn.execute(
        """SELECT run_id, mode, status, trigger, started_at, finished_at,
                  duration_seconds, jobs_fetched, jobs_inserted, jobs_deduped,
                  skills_extracted, error
           FROM pipeline_runs ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_running_runs() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT run_id, mode, started_at FROM pipeline_runs WHERE status = 'running'",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Config ────────────────────────────────────────────────────────────────────

def get_config() -> dict[str, str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM pipeline_config").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def set_config(key: str, value: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """INSERT INTO pipeline_config (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value),
            )
    finally:
        conn.close()


# ── Schedule helpers ──────────────────────────────────────────────────────────

def get_last_run_by_mode(mode: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT started_at, finished_at, status FROM pipeline_runs
               WHERE mode = ? AND status != 'running' ORDER BY started_at DESC LIMIT 1""",
            (mode,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def compute_next_run(mode: str, config: dict) -> str | None:
    """Return ISO string of estimated next run based on last run + interval."""
    from datetime import timedelta

    last = get_last_run_by_mode(mode)
    if not last:
        return None

    try:
        last_dt = datetime.fromisoformat(last["started_at"].replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

    if mode == "ingest-only":
        hours = int(config.get("ingest_interval_hours", 12))
        return (last_dt + timedelta(hours=hours)).isoformat()
    elif mode == "crawl":
        hours = int(config.get("crawl_interval_hours", 4))
        return (last_dt + timedelta(hours=hours)).isoformat()
    return None


# ── Launch ────────────────────────────────────────────────────────────────────

def launch_pipeline(mode: str, extra_args: list[str] | None = None, trigger: str = "manual") -> str:
    """
    Spawn the orchestrator as a detached subprocess and return the run_id.
    The subprocess writes its own start/finish records via the same DB.
    """
    run_id = start_run(mode, trigger=trigger)
    cmd = [sys.executable, "-m", "src.orchestrator", "--mode", mode, "--run-id", run_id]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.Popen(cmd, close_fds=True)
    return run_id
