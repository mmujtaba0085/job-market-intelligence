"""
tests/test_public_viewable_routes.py
─────────────────────────────────────
Verifies web_viewer.py's global_auth_gate() correctly distinguishes three
cases for the newly-public routes:
  1. An anonymous request (no session, no API key) reaches the six page
     routes and eight API routes without being redirected to /auth/login.
  2. Every other route is still fully gated for anonymous requests (the
     public-viewable change must not leak beyond the named routes).
  3. API-key scope enforcement is NOT bypassed by this change - an API
     key without the required scope still gets 403 on a newly-public API
     path, proving the new check didn't accidentally short-circuit the
     scope-check logic further down in global_auth_gate().
"""
import sqlite3

import pytest


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER,
            job_group_id INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL);
        CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b TEXT, co_count INTEGER);
        CREATE TABLE top_titles_summary (title TEXT, count INTEGER);
        CREATE TABLE weekly_metrics (week_start_date TEXT, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, mover_score REAL, emerging_flag INTEGER);
    """)
    # job_detail() needs a real row to return 200 rather than 404 - job_group_id
    # is left NULL so the view function's job_locations lookup (only reached
    # when job_group_id is truthy) is never exercised by this fixture.
    # This exact schema (including weekly_metrics, needed by dashboard_kpis'
    # week-over-week trend comparison) was empirically verified against all
    # 6 page routes and all 8 API routes together before being written here.
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, listing_status) VALUES (1, 'Test Job', 'Test Co', 'active')"
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


def test_anonymous_request_reaches_public_page_routes(anon_client):
    for path in ["/dashboard", "/jobs", "/jobs/1", "/skills/intelligence", "/companies/intelligence", "/titles/analytics"]:
        r = anon_client.get(path)
        assert r.status_code == 200, f"{path} should be reachable anonymously, got {r.status_code}"


def test_anonymous_request_reaches_public_api_routes(anon_client):
    for path in [
        "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/location-diversity",
        "/api/skills/search", "/api/skills/combinations", "/api/companies/list",
        "/api/titles/top", "/api/filters/skills",
    ]:
        r = anon_client.get(path)
        assert r.status_code == 200, f"{path} should be reachable anonymously, got {r.status_code}"


def test_anonymous_request_still_blocked_from_non_public_routes(anon_client):
    r = anon_client.get("/metrics")
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]

    r = anon_client.get("/api/dashboard/trends")
    assert r.status_code == 401


def test_anonymous_request_still_blocked_from_admin_routes(anon_client):
    r = anon_client.get("/admin/auth/users")
    assert r.status_code in (302, 403)


def test_api_key_scope_enforcement_not_bypassed_on_public_api_route(anon_client, monkeypatch):
    import web_viewer
    from src.auth import middleware as auth_middleware

    fake_user = {"id": 99, "role": "viewer", "active": 1}
    monkeypatch.setattr(
        auth_middleware, "_load_user_from_request",
        lambda: (fake_user, "api_key", 1),
    )
    monkeypatch.setattr(auth_middleware, "api_key_has_scope", lambda user, scope: False)

    r = anon_client.get("/api/skills/combinations", headers={"X-API-Key": "jmi_fake"})
    assert r.status_code == 403, "an API key lacking the required scope must still be rejected on a public-viewable API path"


def test_anonymous_root_redirects_to_dashboard_not_login(anon_client):
    """Regression test: the header-brand/logo link on every page points at
    "/" (endpoint `index`), which itself just redirects to /dashboard.
    `index` was missing from _PUBLIC_VIEWABLE_ENDPOINTS, so the auth gate
    blocked it before it ever got a chance to issue that redirect -
    meaning the single most commonly clicked link on the entire site
    (the logo) sent every anonymous visitor to the login page instead of
    the dashboard. Found by testing real production behavior after
    deploy, not caught by any task or review during implementation."""
    r = anon_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/dashboard", (
        f"anonymous visitors hitting '/' must land on /dashboard, not be "
        f"diverted to login - got redirect to {r.headers.get('Location')!r}"
    )
