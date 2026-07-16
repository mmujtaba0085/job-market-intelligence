"""
src/tickets.py
──────────────────
General site feedback/ticketing - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
Deliberately mirrors src/job_reports.py's shape (same separation of pure
validation/rate-limit checks from the create_ticket() insert, same
operational.sqlite placement) - this is the second feature in that pattern
family, not a one-off.

Unlike job_reports, subject and details are BOTH always required regardless
of category (the tickets table's details column is NOT NULL for every row -
a ticket with no content is useless no matter which category it's filed
under, unlike a job report where a predefined category like "spam" is
self-explanatory without extra text).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

CATEGORIES: tuple[str, ...] = ("bug", "feature", "feedback", "other")


def validate_ticket_input(category: str, subject: str, details: str) -> str | None:
    """Returns an error message if invalid, else None."""
    if category not in CATEGORIES:
        return f"Unrecognized category: {category!r}"
    if not subject.strip():
        return "Please provide a short subject line"
    if not details.strip():
        return "Please provide some details"
    return None


def is_rate_limited(conn: sqlite3.Connection, submitter_ip: str, now: datetime) -> bool:
    cutoff = (now - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM tickets WHERE submitter_ip = ? AND created_at >= ?",
        (submitter_ip, cutoff),
    ).fetchone()
    return row["n"] >= 5


def create_ticket(
    conn: sqlite3.Connection, *, category: str, subject: str, details: str,
    submitter_user_id: int | None, submitter_email: str | None, submitter_ip: str, now: datetime,
) -> int:
    cursor = conn.execute(
        """INSERT INTO tickets
           (category, subject, details, submitter_user_id, submitter_email,
            submitter_ip, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
        (category, subject.strip(), details.strip(), submitter_user_id,
         submitter_email, submitter_ip, now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid
