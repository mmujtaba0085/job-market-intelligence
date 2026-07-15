"""
src/notifications.py
──────────────────────
Admin-authored announcement bars, shown to every visitor (including
anonymous ones) on some or all pages. Storage lives in operational.sqlite
(src.storage.db.get_operational_connection()) alongside pipeline_config/
pipeline_runs - this is admin/operational state, not job data.

page_key_for_path() and filter_active_notifications() are pure functions -
no Flask, no I/O - so the filtering logic is testable without a request
context, matching the same separation already used by
src.classification.scheduling.should_process_chunk().
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

PAGE_KEYS: tuple[str, ...] = (
    "dashboard", "jobs", "skills", "companies", "titles", "metrics", "api_docs",
)

_PATH_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/dashboard", "dashboard"),
    ("/jobs", "jobs"),
    ("/skills", "skills"),
    ("/companies", "companies"),
    ("/titles", "titles"),
    ("/metrics", "metrics"),
    ("/api/docs", "api_docs"),
)


def page_key_for_path(path: str) -> str | None:
    """Maps a request path to one of PAGE_KEYS, or None if the path isn't
    in any targetable section (e.g. /admin/*, /auth/*, /healthz)."""
    if path == "/":
        return "dashboard"
    for prefix, key in _PATH_PREFIXES:
        if path.startswith(prefix):
            return key
    return None


def filter_active_notifications(
    rows: list[sqlite3.Row],
    path: str,
    dismissed_ids: set[int],
    now: datetime,
) -> list[sqlite3.Row]:
    """rows: notifications table rows already filtered to removed_at IS NULL
    by the caller's SQL query (see load_active_notifications() below) - this
    function only handles page-matching, expiry, and dismissal, the parts
    that need `path`/`now`/`dismissed_ids` rather than a plain WHERE clause."""
    page_key = page_key_for_path(path)
    result = []
    for row in rows:
        if row["id"] in dismissed_ids:
            continue
        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                from datetime import timezone as _tz
                expires_at = expires_at.replace(tzinfo=_tz.utc)
            if now >= expires_at:
                continue
        targets = row["target_pages"]
        if targets == "all":
            result.append(row)
        elif page_key and page_key in targets.split(","):
            result.append(row)
    return result


def load_active_notifications(path: str, dismissed_ids: set[int], now: datetime) -> list[sqlite3.Row]:
    """Query + filter in one call - the function web_viewer.py's
    before_request hook actually calls."""
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    try:
        rows = conn.execute(
            "SELECT id, heading, body, severity, target_pages, expires_at FROM notifications WHERE removed_at IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return filter_active_notifications(rows, path, dismissed_ids, now)
