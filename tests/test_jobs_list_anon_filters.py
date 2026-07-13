"""
tests/test_jobs_list_anon_filters.py
─────────────────────────────────────
Filtering /jobs is a signed-in feature: the filter sidebar is hidden for
anonymous visitors (gated client-side in static/js/filters.js behind
window.GW_AUTHED), but a filtered URL can still be typed or shared
directly. These tests confirm the server ignores filter query params for
anonymous requests too - not just hides the UI - while signed-in requests
still get real filtering.
"""
import sqlite3

import pytest


@pytest.fixture()
def jobs_app(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL);
    """)
    conn.executemany(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "Alpha Job", "Alpha Co", "2026-01-05", "2026-01-05T00:00:00", "A", "m1", "active"),
            (2, "Beta Job", "Beta Co", "2026-01-04", "2026-01-04T00:00:00", "A", "m2", "active"),
        ],
    )
    conn.execute(
        "INSERT INTO skills (job_id, raw_detected_skill, normalized_skill, category, confidence_score) "
        "VALUES (1, 'Python', 'python', 'language', 1.0)"
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


@pytest.fixture()
def signed_in_client(jobs_app):
    with jobs_app.session_transaction() as sess:
        sess["user_id"] = 1
    return jobs_app


class TestAnonymousFiltersIgnored:
    def test_market_filter_ignored_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?market=m1")
        html = response.get_data(as_text=True)
        assert "Alpha Job" in html
        assert "Beta Job" in html  # market=m2, would be excluded if filter were honored
        assert "(2)</span>" in html  # "Jobs (2)" heading count

    def test_company_filter_ignored_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?company=Alpha")
        html = response.get_data(as_text=True)
        assert "Beta Job" in html

    def test_search_filter_ignored_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?search=Alpha")
        html = response.get_data(as_text=True)
        assert "Beta Job" in html

    def test_skills_filter_ignored_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?skills=python")
        html = response.get_data(as_text=True)
        assert "Beta Job" in html  # has no skills row at all, would be excluded if honored

    def test_status_filter_ignored_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?status=closed")
        html = response.get_data(as_text=True)
        # Both jobs are 'active' - a real status=closed filter would show zero.
        assert "Alpha Job" in html
        assert "Beta Job" in html

    def test_no_active_filters_badge_shown_for_anonymous(self, jobs_app):
        response = jobs_app.get("/jobs?market=m1&company=Alpha")
        html = response.get_data(as_text=True)
        assert "Active Filters:" not in html


class TestSignedInFiltersStillWork:
    def test_market_filter_applies_for_signed_in_user(self, signed_in_client):
        response = signed_in_client.get("/jobs?market=m1")
        html = response.get_data(as_text=True)
        assert "Alpha Job" in html
        assert "Beta Job" not in html
        assert "(1)</span>" in html  # "Jobs (1)" heading count

    def test_active_filters_badge_shown_for_signed_in_user(self, signed_in_client):
        response = signed_in_client.get("/jobs?market=m1")
        html = response.get_data(as_text=True)
        assert "Active Filters:" in html
