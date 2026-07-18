"""
tests/test_dashboard_top_it_widgets.py
──────────────────────────────────────────
Regression coverage for /api/dashboard/top-it-jobs and
/api/dashboard/top-it-companies - see
docs/superpowers/specs/2026-07-17-dashboard-region-restructure-design.md.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def it_widgets_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT,
            field_category_id TEXT, source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE weekly_metrics (market_id TEXT, week_start_date TEXT, week_number INTEGER, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, absolute_delta INTEGER, mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    today = datetime.now().strftime("%Y-%m-%d")
    with conn:
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK IT Job 1', 'Devsinc', 'Pakistan', 'it.software', ?)", (today,))
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK IT Job 2', 'Devsinc', 'Pakistan', 'it.data', ?)", (today,))
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('US IT Job', 'NVIDIA', 'United States', 'it.software', ?)", (today,))
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK Nurse Job', 'Shaukat Khanum', 'Pakistan', 'healthcare.clinical', ?)", (today,))
        # A Pakistan Jobs Bank parsing bug leaks a bare location into the
        # company field instead of a real employer name - see
        # _LOCATION_LEAKED_AS_COMPANY in web_viewer.py.
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('Mislabeled Job 1', 'Pakistan', 'Pakistan', 'it.software', ?)", (today,))
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('Mislabeled Job 2', 'Pakistan', 'Pakistan', 'it.data', ?)", (today,))
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client


def test_top_it_jobs_defaults_to_pakistan_scope(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-jobs")
    assert r.status_code == 200
    titles = [j["title"] for j in r.get_json()]
    assert "PK IT Job 1" in titles
    assert "PK IT Job 2" in titles
    assert "US IT Job" not in titles
    assert "PK Nurse Job" not in titles


def test_top_it_jobs_region_all_still_applies_it_filter(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-jobs?region=all")
    assert r.status_code == 200
    titles = [j["title"] for j in r.get_json()]
    assert "PK IT Job 1" in titles
    assert "PK IT Job 2" in titles
    assert "US IT Job" in titles
    assert "PK Nurse Job" not in titles


def test_top_it_companies_defaults_to_pakistan_scope(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-companies")
    assert r.status_code == 200
    companies = [c["company"] for c in r.get_json()]
    assert "Devsinc" in companies
    assert "NVIDIA" not in companies
    assert "Shaukat Khanum" not in companies


def test_top_it_companies_region_all_includes_worldwide_it_companies(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-companies?region=all")
    assert r.status_code == 200
    companies = [c["company"] for c in r.get_json()]
    assert "Devsinc" in companies
    assert "NVIDIA" in companies
    assert "Shaukat Khanum" not in companies


def test_top_it_jobs_no_backfill_from_non_it_when_scarce(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-jobs")
    assert len(r.get_json()) == 2


def test_top_it_jobs_respects_status_window(it_widgets_client):
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('Old PK IT Job', 'Contour', 'Pakistan', 'it.software', ?)", (old_date,))
    conn.commit()
    conn.close()

    r_active = it_widgets_client.get("/api/dashboard/top-it-jobs?status=active")
    assert "Old PK IT Job" not in [j["title"] for j in r_active.get_json()]

    r_all = it_widgets_client.get("/api/dashboard/top-it-jobs?status=all")
    assert "Old PK IT Job" in [j["title"] for j in r_all.get_json()]


def test_location_diversity_route_no_longer_exists(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/location-diversity")
    assert r.status_code == 404


def test_top_it_jobs_excludes_location_leaked_as_company(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-jobs")
    titles = [j["title"] for j in r.get_json()]
    assert "Mislabeled Job 1" not in titles
    assert "Mislabeled Job 2" not in titles


def test_top_it_companies_excludes_location_leaked_as_company(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-companies")
    companies = [c["company"] for c in r.get_json()]
    assert "Pakistan" not in companies
