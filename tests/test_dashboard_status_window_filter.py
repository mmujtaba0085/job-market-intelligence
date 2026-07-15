"""
tests/test_dashboard_status_window_filter.py
───────────────────────────────────────────────
Regression coverage for the "Listings" dropdown (?status=) actually being
honored by the /api/dashboard/* widgets, not just /jobs - see
web_viewer.py::_status_window_clause. Before this, every dashboard widget
silently ignored the query param dashboard.js already sent on every
request (see static/js/dashboard.js::dashboardApi()).

Covers dashboard_kpis, dashboard_top-skills (fallback path), sources,
companies, and location-diversity. dashboard_geo has its own dedicated
file, tests/test_dashboard_geo_endpoint.py, extended alongside today's
bucketing fix rather than duplicated here. dashboard_trends/emerging/
declining are explicitly out of scope - they read weekly_metrics, a
pre-aggregated-by-week table with no per-job age/status to filter on.

Follows the fixture pattern established in tests/test_dashboard_geo_endpoint.py
and tests/test_jobs_list_sort.py: a minimal hand-rolled sqlite schema
monkeypatched onto every rotating-DB target, and a signed-in session
(dashboard_geo/top-skills/sources are gated; kpis/companies/location-
diversity are also exercised signed-in here for consistency, since
_PUBLIC_API_READS' anonymous carve-out is orthogonal to this feature).

Three jobs are seeded, each with a distinct company/source/remote_type so
every metric's active-vs-all difference is unambiguous:
  - "Recent Job": Acme / SourceX / remote, posted 2 days ago, location_count=1,
    skill "python". Inside the window.
  - "Old Job": Beta Co / SourceY / on-site, posted 45 days ago, location_count=2,
    skill "cobol". Outside the window - must disappear under status=active.
  - "Fallback Job": Gamma Inc / SourceZ / hybrid, posted_date NULL but
    first_seen_at 2 days old, location_count=3, skill "rust". No posted_date -
    must still count as active via the posted_date -> first_seen_at fallback.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def dash_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER,
            first_seen_at TEXT, job_group_id INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE skills (
            job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT,
            category TEXT, confidence_score REAL
        );
        CREATE TABLE weekly_metrics (
            week_start_date TEXT, skill_name TEXT, category TEXT, frequency INTEGER,
            growth_percentage REAL, mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    today = datetime.now()
    recent_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    old_date = (today - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_first_seen = (today - timedelta(days=2)).isoformat()

    conn.execute(
        "INSERT INTO jobs (job_id, title, company, source_name, remote_type, posted_date, "
        "ingested_at, listing_status, location_count, job_group_id) "
        "VALUES (1, 'Recent Job', 'Acme', 'SourceX', 'remote', ?, ?, 'active', 1, 1)",
        (recent_date, f"{recent_date}T00:00:00"),
    )
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, source_name, remote_type, posted_date, "
        "ingested_at, listing_status, location_count, job_group_id) "
        "VALUES (2, 'Old Job', 'Beta Co', 'SourceY', 'on-site', ?, ?, 'active', 2, 2)",
        (old_date, f"{old_date}T00:00:00"),
    )
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, source_name, remote_type, posted_date, "
        "ingested_at, listing_status, location_count, job_group_id, first_seen_at) "
        "VALUES (3, 'Fallback Job', 'Gamma Inc', 'SourceZ', 'hybrid', NULL, ?, 'active', 3, 3, ?)",
        (f"{recent_date}T00:00:00", recent_first_seen),
    )
    conn.executemany(
        "INSERT INTO skills (job_id, raw_detected_skill, normalized_skill, category, confidence_score) "
        "VALUES (?,?,?,?,1.0)",
        [
            (1, "Python", "python", "language"),
            (2, "COBOL", "cobol", "language"),
            (3, "Rust", "rust", "language"),
        ],
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


# ── dashboard_kpis ──────────────────────────────────────────────────────────

def test_kpis_status_active_excludes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/kpis?status=active")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total_jobs"] == 2, body  # Recent + Fallback, Old excluded
    assert body["active_sources"] == 2, body  # SourceX + SourceZ, SourceY excluded
    assert body["remote_pct"] == 50.0, body  # 1 remote (Recent) of 2 in-window jobs


def test_kpis_status_all_includes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/kpis?status=all")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total_jobs"] == 3, body
    assert body["active_sources"] == 3, body
    # dashboard_kpis() itself rounds remote_pct to 1 decimal place.
    assert body["remote_pct"] == round(100.0 / 3, 1), body


def test_kpis_default_status_matches_active(dash_client):
    r = dash_client.get("/api/dashboard/kpis")
    body = r.get_json()
    assert body["total_jobs"] == 2, body


def test_kpis_total_skills_is_status_window_aware(dash_client):
    """total_skills used to query the bare `skills` table with no relation
    to job age/status at all - it must now be scoped to skills belonging to
    in-scope jobs, same as total_jobs."""
    r_active = dash_client.get("/api/dashboard/kpis?status=active")
    assert r_active.get_json()["total_skills"] == 2  # python, rust (cobol's job is old)

    r_all = dash_client.get("/api/dashboard/kpis?status=all")
    assert r_all.get_json()["total_skills"] == 3  # python, cobol, rust


# ── dashboard_top-skills (fallback path - weekly_metrics is empty here,
#    so MAX(week_start_date) is NULL and the primary query always misses,
#    guaranteeing the skills/active_jobs join fallback runs) ────────────────

def test_top_skills_status_active_excludes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/top-skills?status=active")
    assert r.status_code == 200
    skills = {row["skill"] for row in r.get_json()}
    assert skills == {"python", "rust"}, skills


def test_top_skills_status_all_includes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/top-skills?status=all")
    assert r.status_code == 200
    skills = {row["skill"] for row in r.get_json()}
    assert skills == {"python", "cobol", "rust"}, skills


# ── dashboard_sources ────────────────────────────────────────────────────

def test_sources_status_active_excludes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/sources?status=active")
    assert r.status_code == 200
    sources = {row["source"]: row["count"] for row in r.get_json()}
    assert "SourceY" not in sources, sources
    assert sources.get("SourceX") == 1
    assert sources.get("SourceZ") == 1


def test_sources_status_all_includes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/sources?status=all")
    sources = {row["source"]: row["count"] for row in r.get_json()}
    assert sources.get("SourceY") == 1, sources


# ── dashboard_companies ─────────────────────────────────────────────────

def test_companies_status_active_excludes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/companies?status=active")
    assert r.status_code == 200
    companies = {row["company"]: row["count"] for row in r.get_json()}
    assert "Beta Co" not in companies, companies
    assert companies.get("Acme") == 1
    assert companies.get("Gamma Inc") == 1


def test_companies_status_all_includes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/companies?status=all")
    companies = {row["company"]: row["count"] for row in r.get_json()}
    assert companies.get("Beta Co") == 1, companies


def test_companies_fallback_job_counted_as_active(dash_client):
    """'Fallback Job' (Gamma Inc) has no posted_date at all - only a recent
    first_seen_at - and must still appear under status=active."""
    r = dash_client.get("/api/dashboard/companies?status=active")
    companies = {row["company"] for row in r.get_json()}
    assert "Gamma Inc" in companies


# ── dashboard_location-diversity ────────────────────────────────────────
# Base query already restricts to location_count > 1 regardless of status,
# which excludes "Recent Job" (location_count=1) from every case below -
# only "Old Job" (2) and "Fallback Job" (3) are ever candidates.

def test_location_diversity_status_active_excludes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/location-diversity?status=active")
    assert r.status_code == 200
    companies = {row["company"] for row in r.get_json()}
    assert "Beta Co" not in companies, companies
    assert "Gamma Inc" in companies


def test_location_diversity_status_all_includes_old_job(dash_client):
    r = dash_client.get("/api/dashboard/location-diversity?status=all")
    companies = {row["company"] for row in r.get_json()}
    assert "Beta Co" in companies, companies
    assert "Gamma Inc" in companies
