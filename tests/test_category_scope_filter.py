"""
tests/test_category_scope_filter.py
───────────────────────────────────────
Regression coverage for the /jobs Category toggle (IT/All Categories) -
see docs/superpowers/specs/2026-07-17-it-priority-launch-readiness-design.md
Part 2. Deliberately NULL-inclusive, unlike the dashboard's strict IT
widgets - see tests/test_dashboard_top_it_widgets.py for that contrast.
"""
import sqlite3

import pytest


@pytest.fixture()
def category_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT DEFAULT 'Pakistan',
            field_category_id TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
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

    with conn:
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed IT Job 1', 'it.software')")
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed IT Job 2', 'it.data')")
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Unclassified Job', NULL)")
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed Nurse Job', 'healthcare.clinical')")
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


def test_category_scope_clause_it_is_null_inclusive():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("it") == " AND (field_category_id IS NULL OR field_category_id LIKE 'it.%')"


def test_category_scope_clause_all_is_unrestricted():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("all") == ""


def test_category_scope_clause_unrecognized_value_is_unrestricted():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("bogus") == ""


def test_category_scope_clause_applies_alias_prefix():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("it", "j.") == " AND (j.field_category_id IS NULL OR j.field_category_id LIKE 'it.%')"


def test_jobs_list_defaults_to_it_scope(category_client):
    r = category_client.get("/jobs")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Confirmed IT Job 1" in body
    assert "Confirmed IT Job 2" in body
    assert "Confirmed Nurse Job" not in body


def test_jobs_list_it_scope_includes_unclassified_jobs(category_client):
    r = category_client.get("/jobs")
    assert "Unclassified Job" in r.get_data(as_text=True)


def test_jobs_list_category_all_shows_every_category(category_client):
    r = category_client.get("/jobs?category=all")
    body = r.get_data(as_text=True)
    assert "Confirmed IT Job 1" in body
    assert "Confirmed IT Job 2" in body
    assert "Unclassified Job" in body
    assert "Confirmed Nurse Job" in body


def test_category_defaults_to_cookie_value_when_no_query_param(category_client):
    category_client.set_cookie("jmi_category", "all")
    r = category_client.get("/jobs")
    assert "Confirmed Nurse Job" in r.get_data(as_text=True)


def test_category_explicit_query_param_overrides_cookie(category_client):
    category_client.set_cookie("jmi_category", "all")
    r = category_client.get("/jobs?category=it")
    assert "Confirmed Nurse Job" not in r.get_data(as_text=True)


def test_category_not_reset_for_anonymous_visitor(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT DEFAULT 'Pakistan',
            field_category_id TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    with conn:
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed Nurse Job', 'healthcare.clinical')")
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

    r = client.get("/jobs?category=all")
    assert "Confirmed Nurse Job" in r.get_data(as_text=True)
