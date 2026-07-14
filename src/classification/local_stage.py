"""
Runs the existing, unchanged src.market_classifier.classify_job() against
jobs, writing straight to jobs.field_category_id (never jobs.market_id -
that column is the live ingestion-source grouping used by the Jobs List
Market filter, a completely different concept from this taxonomy).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.market_classifier import classify_job
from src.pipeline_monitor import get_config

DEFAULT_CONFIDENCE_THRESHOLD = 0.62


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _confidence_threshold() -> float:
    # NOTE: there is no separate "score threshold" knob here - classify_job()
    # already enforces its own internal 2.0 raw-score cutoff before ever
    # returning a non-None market_id (see market_classifier.py), and its
    # MarketMatch return value doesn't expose the raw score for a caller to
    # re-check. Only `confidence` is re-checkable at this layer.
    cfg = get_config()
    return float(cfg.get("classification_confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))


def _classify_one(conn, run_id: str, job_id: int, title: str, description: str, confidence_threshold: float) -> bool:
    """Classify a single job; returns True if it was directly classified, False if queued for Groq."""
    match = classify_job(title, description or "")
    now = _now()

    # Reclassification replaces the prior assignment picture entirely - clear
    # any existing rows for this job before writing fresh ones below, so a
    # category change or a classified->unclassified transition on a
    # reclassify_all() run doesn't leave stale/duplicate rows behind (a job
    # can only have one 'primary' category at a time).
    conn.execute("DELETE FROM job_category_assignments WHERE job_id = ?", (job_id,))

    # classify_job() already applies its own internal threshold (0.62/2.0) and
    # returns market_id=None below it - re-checking confidence here lets an
    # admin raise the bar higher via config without touching market_classifier.py.
    if match.market_id and match.confidence >= confidence_threshold:
        conn.execute(
            """UPDATE jobs SET field_category_id = ?, field_classification_confidence = ?,
                                field_classification_method = ?, field_classification_attempted_at = ?
               WHERE job_id = ?""",
            (match.market_id, match.confidence, match.method, now, job_id),
        )
        conn.execute(
            """INSERT INTO job_category_assignments
               (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
               VALUES (?, ?, 'primary', ?, ?, ?, ?)""",
            (job_id, match.market_id, match.confidence, match.method, json.dumps(match.evidence), now),
        )
        for tag in match.tags:
            conn.execute(
                """INSERT INTO job_category_assignments
                   (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
                   VALUES (?, ?, 'tag', ?, ?, ?, ?)""",
                (job_id, tag, match.confidence, match.method, json.dumps(match.evidence), now),
            )
        return True

    # Below threshold (or unclassifiable): explicitly clear any prior
    # classification on this job, not just leave stale values in place - a
    # job that was classified before a config/taxonomy change and no longer
    # qualifies must not keep showing an old field_category_id while
    # simultaneously being queued for Groq review as "unclassified."
    conn.execute(
        """UPDATE jobs SET field_category_id = NULL, field_classification_confidence = NULL,
                            field_classification_method = NULL, field_classification_attempted_at = ?
           WHERE job_id = ?""",
        (now, job_id),
    )
    conn.execute(
        """INSERT OR IGNORE INTO groq_classification_queue (job_id, status, created_at)
           VALUES (?, 'pending', ?)""",
        (job_id, now),
    )
    return False


def _run_batch(conn, run_id: str, rows: list) -> dict[str, int]:
    # Read once per batch, not once per job - avoids ~500 redundant
    # get_config() round-trips on a full local_full_backfill chunk for a
    # value that can't change mid-batch anyway.
    confidence_threshold = _confidence_threshold()
    processed = classified = queued = 0
    cursor_job_id = None
    for row in rows:
        did_classify = _classify_one(conn, run_id, row["job_id"], row["title"], row["raw_description"], confidence_threshold)
        processed += 1
        cursor_job_id = row["job_id"]
        if did_classify:
            classified += 1
        else:
            queued += 1

    conn.execute(
        # COALESCE(?, cursor_job_id) - an empty batch (rows=[]) leaves
        # cursor_job_id as the Python None it started as; without COALESCE
        # that would overwrite an existing cursor back to NULL, which would
        # restart a local_full_backfill run from the beginning on its next
        # tick instead of leaving the cursor where a prior chunk left it.
        """UPDATE classification_runs
           SET jobs_processed = jobs_processed + ?, jobs_classified = jobs_classified + ?,
               jobs_queued_groq = jobs_queued_groq + ?, cursor_job_id = COALESCE(?, cursor_job_id)
           WHERE run_id = ?""",
        (processed, classified, queued, cursor_job_id, run_id),
    )
    conn.commit()
    return {"processed": processed, "classified": classified, "queued_groq": queued}


def classify_pending_jobs(conn, run_id: str, limit: int | None = None) -> dict[str, int]:
    """Classify jobs that have never been attempted (field_classification_attempted_at IS NULL)."""
    query = "SELECT job_id, title, raw_description FROM jobs WHERE field_classification_attempted_at IS NULL ORDER BY job_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    return _run_batch(conn, run_id, rows)


def reclassify_all(conn, run_id: str, limit: int | None = None, after_job_id: int | None = None) -> dict[str, int]:
    """Re-run classification for every job, regardless of prior attempts.

    after_job_id resumes a chunked local_full_backfill run from where the
    previous chunk's cursor_job_id left off - without it, every call would
    re-select the same first `limit` job_ids by job_id order forever, and a
    multi-tick backfill would never advance past its first chunk."""
    query = "SELECT job_id, title, raw_description FROM jobs"
    params: list = []
    if after_job_id is not None:
        query += " WHERE job_id > ?"
        params.append(after_job_id)
    query += " ORDER BY job_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query, params).fetchall()
    return _run_batch(conn, run_id, rows)
