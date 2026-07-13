"""
Runs the existing, unchanged src.market_classifier.classify_job() against
jobs, writing straight to jobs.field_category_id (never jobs.market_id -
that column is the live ingestion-source grouping used by the Jobs List
Market filter, a completely different concept from this taxonomy).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.market_classifier import classify_job
from src.pipeline_monitor import get_config

DEFAULT_CONFIDENCE_THRESHOLD = 0.62
DEFAULT_SCORE_THRESHOLD = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thresholds() -> tuple[float, float]:
    cfg = get_config()
    confidence = float(cfg.get("classification_confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
    score = float(cfg.get("classification_score_threshold", DEFAULT_SCORE_THRESHOLD))
    return confidence, score


def _classify_one(conn, run_id: str, job_id: int, title: str, description: str) -> bool:
    """Classify a single job; returns True if it was directly classified, False if queued for Groq."""
    match = classify_job(title, description or "")
    confidence_threshold, score_threshold = _thresholds()
    now = _now()

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
            """INSERT OR REPLACE INTO job_category_assignments
               (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
               VALUES (?, ?, 'primary', ?, ?, ?, ?)""",
            (job_id, match.market_id, match.confidence, match.method, json.dumps(match.evidence), now),
        )
        for tag in match.tags:
            conn.execute(
                """INSERT OR REPLACE INTO job_category_assignments
                   (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
                   VALUES (?, ?, 'tag', ?, ?, ?, ?)""",
                (job_id, tag, match.confidence, match.method, json.dumps(match.evidence), now),
            )
        return True

    conn.execute(
        "UPDATE jobs SET field_classification_attempted_at = ? WHERE job_id = ?",
        (now, job_id),
    )
    conn.execute(
        """INSERT OR IGNORE INTO groq_classification_queue (job_id, status, created_at)
           VALUES (?, 'pending', ?)""",
        (job_id, now),
    )
    return False


def _run_batch(conn, run_id: str, rows: list, ) -> dict[str, int]:
    processed = classified = queued = 0
    cursor_job_id = None
    for row in rows:
        did_classify = _classify_one(conn, run_id, row["job_id"], row["title"], row["raw_description"])
        processed += 1
        cursor_job_id = row["job_id"]
        if did_classify:
            classified += 1
        else:
            queued += 1

    conn.execute(
        """UPDATE classification_runs
           SET jobs_processed = jobs_processed + ?, jobs_classified = jobs_classified + ?,
               jobs_queued_groq = jobs_queued_groq + ?, cursor_job_id = ?
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


def reclassify_all(conn, run_id: str, limit: int | None = None) -> dict[str, int]:
    """Re-run classification for every job, regardless of prior attempts."""
    query = "SELECT job_id, title, raw_description FROM jobs ORDER BY job_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    return _run_batch(conn, run_id, rows)
