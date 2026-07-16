"""
src/job_reports.py
─────────────────────
Per-job "Report this listing" feature - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
Storage lives in operational.sqlite (src.storage.db.get_operational_connection()),
same reasoning already applied to notifications - this is admin/operational
state about a job, not job data itself.

validate_report_input() and is_rate_limited() are pure/query-only checks the
caller (the Flask route) runs BEFORE calling create_report() - this module
doesn't enforce them itself, matching the same separation of pure logic
from I/O already used by src.notifications.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

REASON_CATEGORIES: tuple[str, ...] = (
    "incorrect_info", "wrong_category", "broken_link", "spam", "other",
)


def validate_report_input(reason_category: str, details: str) -> str | None:
    """Returns an error message if invalid, else None."""
    if reason_category not in REASON_CATEGORIES:
        return f"Unrecognized reason category: {reason_category!r}"
    if reason_category == "other" and not details.strip():
        return "Please provide details when selecting 'Other'"
    return None


def is_rate_limited(conn: sqlite3.Connection, reporter_ip: str, now: datetime) -> bool:
    cutoff = (now - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM job_reports WHERE reporter_ip = ? AND created_at >= ?",
        (reporter_ip, cutoff),
    ).fetchone()
    return row["n"] >= 5


def create_report(
    conn: sqlite3.Connection, *, job_id: int | None, job_url: str, job_title: str,
    reason_category: str, details: str, reporter_user_id: int | None,
    reporter_email: str | None, reporter_ip: str, now: datetime,
) -> int:
    cursor = conn.execute(
        """INSERT INTO job_reports
           (job_id, job_url, job_title, reason_category, details,
            reporter_user_id, reporter_email, reporter_ip, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (job_id, job_url, job_title, reason_category, details or None,
         reporter_user_id, reporter_email, reporter_ip, now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid
