"""
tests/test_jobs_list_status_window.py
──────────────────────────────────────
Regression coverage for the "Active" listing-status filter on /jobs also
meaning "within the last month", not just listing_status IS NULL/'active'
(see web_viewer.py::_status_window_clause). Follows the fixture pattern
established in tests/test_jobs_list_sort.py: a minimal hand-rolled sqlite
schema monkeypatched onto every rotating-DB target, and a signed-in session
(status is a signed-in-only filter - anonymous requests always hard-code
status=active regardless of the query string, per
tests/test_jobs_list_anon_filters.py).

Three jobs are seeded:
  - "Recent Job": posted 2 days ago - inside the window.
  - "Old Job": posted 45 days ago - outside the window.
  - "Fallback Job": posted_date is NULL, first_seen_at is 2 days ago - the
    posted_date -> first_seen_at fallback (same pattern already used for
    date *display* in templates/jobs_list.html) must still count it as
    active.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def signed_in_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT 'Pakistan',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER,
            first_seen_at TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL);
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    today = datetime.now()
    recent_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    old_date = (today - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_first_seen = (today - timedelta(days=2)).isoformat()

    conn.execute(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status) "
        "VALUES (1, 'Recent Job', 'Co', ?, ?, 'A', 'm1', 'active')",
        (recent_date, f"{recent_date}T00:00:00"),
    )
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status) "
        "VALUES (2, 'Old Job', 'Co', ?, ?, 'A', 'm1', 'active')",
        (old_date, f"{old_date}T00:00:00"),
    )
    # No posted_date at all - only first_seen_at, which is recent.
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status, first_seen_at) "
        "VALUES (3, 'Fallback Job', 'Co', NULL, ?, 'A', 'm1', 'active', ?)",
        (f"{recent_date}T00:00:00", recent_first_seen),
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client


def test_status_active_excludes_job_older_than_one_month(signed_in_client):
    response = signed_in_client.get("/jobs?status=active")
    html = response.get_data(as_text=True)
    assert "Old Job" not in html, "a job posted 45 days ago must not appear under status=active"
    assert "Recent Job" in html
    assert "(2)</span>" in html  # Recent Job + Fallback Job only


def test_status_all_includes_job_older_than_one_month(signed_in_client):
    response = signed_in_client.get("/jobs?status=all")
    html = response.get_data(as_text=True)
    assert "Old Job" in html, "status=all must include jobs regardless of age"
    assert "Recent Job" in html
    assert "(3)</span>" in html


def test_default_status_matches_explicit_all(signed_in_client):
    """No ?status= at all must behave like ?status=all, not ?status=active -
    the sitewide default flipped (see
    docs/superpowers/plans/2026-07-16-pakistan-first-default-experience.md
    Task 1) so a first-time visitor isn't shown an overly narrow view once
    the region filter is also narrowing the default scope."""
    response = signed_in_client.get("/jobs")
    html = response.get_data(as_text=True)
    assert "Old Job" in html
    assert "(3)</span>" in html


def test_status_active_fallback_to_first_seen_at_when_posted_date_missing(signed_in_client):
    """'Fallback Job' has posted_date = NULL but a first_seen_at only 2 days
    old - it must still be counted as active via the
    COALESCE(posted_date, first_seen_at) fallback, not dropped just because
    posted_date itself is missing."""
    response = signed_in_client.get("/jobs?status=active")
    html = response.get_data(as_text=True)
    assert "Fallback Job" in html
