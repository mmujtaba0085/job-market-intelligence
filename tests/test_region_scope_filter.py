"""
tests/test_region_scope_filter.py
─────────────────────────────────────
Regression coverage for the Pakistan-first default region scope - see
docs/superpowers/specs/2026-07-16-pakistan-first-default-experience-design.md.

Follows the fixture pattern established in tests/test_dashboard_geo_endpoint.py:
a minimal hand-rolled sqlite schema monkeypatched onto every rotating-DB
target, a signed-in session where a route requires one.
"""
import sqlite3

import pytest


@pytest.fixture()
def region_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER, field_category_id TEXT
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE weekly_metrics (market_id TEXT, week_start_date TEXT, week_number INTEGER, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, absolute_delta INTEGER, mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    with conn:
        for country in ("Pakistan", "Pakistan", "Global", "United States", "Germany"):
            conn.execute("INSERT INTO jobs (country) VALUES (?)", (country,))
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


def test_region_scope_clause_pk_restricts_to_pakistan_and_global():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk") == " AND country IN ('Pakistan', 'Global')"


def test_region_scope_clause_all_is_unrestricted():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("all") == ""


def test_region_scope_clause_unrecognized_value_is_unrestricted():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("bogus") == ""


def test_region_scope_clause_applies_alias_prefix():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk", "j.") == " AND j.country IN ('Pakistan', 'Global')"


def test_region_scope_clause_pk_only_is_strict_pakistan():
    """pk_only excludes 'Global' too, unlike 'pk' - used by the dashboard's
    "See all IT jobs" link so it matches the strict country='Pakistan'
    scope its own widget already shows, instead of letting high-volume
    'Global' postings (many not actually Pakistan-relevant) crowd out
    real Pakistan jobs. Confirmed live 2026-07-18."""
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk_only") == " AND country = 'Pakistan'"


def test_region_scope_clause_pk_only_applies_alias_prefix():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk_only", "j.") == " AND j.country = 'Pakistan'"


def test_jobs_list_region_pk_only_excludes_global(region_client):
    r = region_client.get("/jobs?region=pk_only")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "(2)</span>" in body  # 2 Pakistan only, the 1 Global row excluded


def test_jobs_list_region_pk_only_active_filters_badge(region_client):
    r = region_client.get("/jobs?region=pk_only")
    body = r.get_data(as_text=True)
    assert "Region: Pakistan Only" in body
    assert "Region: All Countries" not in body


def test_jobs_list_defaults_to_pakistan_scope(region_client):
    r = region_client.get("/jobs")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "(3)</span>" in body  # 2 Pakistan + 1 Global


def test_jobs_list_region_all_shows_every_country(region_client):
    r = region_client.get("/jobs?region=all")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "(5)</span>" in body


def test_dashboard_geo_is_not_affected_by_region_scope(region_client):
    """dashboard_geo() is deliberately excluded from the region filter - it
    must always show the true, unrestricted country breakdown, since
    restricting the same column it groups by would make the widget
    degenerate. Confirmed via the bucket-sum-equals-total invariant already
    established in test_dashboard_geo_endpoint.py - here, specifically,
    proving region=pk does NOT shrink the total the way it does for kpis."""
    r_default = region_client.get("/api/dashboard/geo")
    r_all = region_client.get("/api/dashboard/geo?region=all")
    total_default = sum(row["count"] for row in r_default.get_json())
    total_all = sum(row["count"] for row in r_all.get_json())
    assert total_default == 5, f"geo must show all 5 jobs regardless of region, got {total_default}"
    assert total_all == 5


def test_status_defaults_to_all_not_active(region_client):
    """The Active/Historical status default flips from 'active' (last
    month only) to 'all' (everything) - two restrictive defaults (region +
    age) compounded would show a first-time visitor too little. Seed one
    old Pakistan job (outside the last-month window) and confirm it's
    included by default without any ?status= override. Uses /jobs, not
    /api/dashboard/kpis - the dashboard KPIs route no longer applies
    region scoping at all as of the dashboard-region-restructure plan."""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/jobs")  # no ?status= at all
    assert "(4)</span>" in r.get_data(as_text=True), "old Pakistan job must be included - status defaults to 'all', not 'active'"


def test_region_defaults_to_cookie_value_when_no_query_param(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/jobs")
    assert "(5)</span>" in r.get_data(as_text=True)  # cookie says 'all', no ?region= override


def test_explicit_query_param_overrides_cookie(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/jobs?region=pk")
    assert "(3)</span>" in r.get_data(as_text=True)  # explicit ?region=pk wins over the 'all' cookie


def test_default_is_pakistan_when_neither_cookie_nor_query_param_present(region_client):
    r = region_client.get("/jobs")
    assert "(3)</span>" in r.get_data(as_text=True)


def test_region_and_status_filters_compose_correctly_when_both_explicit(region_client):
    """Seed one Pakistan job that's old (outside the active window) - with
    status=active EXPLICITLY requested, it must be excluded regardless of
    region, proving the two filters combine with AND, not one silently
    overriding the other. Uses /jobs - see test_status_defaults_to_all_not_active
    above for why /api/dashboard/kpis is no longer the right vehicle for
    this."""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/jobs?status=active")  # region=pk (default), status=active (explicit)
    assert "(3)</span>" in r.get_data(as_text=True)  # the old Pakistan job must NOT be included under status=active
