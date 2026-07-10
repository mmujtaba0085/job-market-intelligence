"""
tests/test_route_caching.py
──────────────────────────────
Integration test: hits a real cached route through the actual running app
(not a synthetic test app like tests/test_cache_key.py's unit tests) and
confirms the second identical request is served from cache, via
Flask-Caching's response_hit_indication mechanism (a `hit_cache: True`
response header, present only on cache hits).
"""
import sqlite3

import pytest


@pytest.fixture()
def cached_app(tmp_path, monkeypatch):
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
    """)
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1  # matches the established pattern in tests/test_jobs_list_sort.py
    return client


def test_dashboard_second_request_is_served_from_cache(cached_app):
    r1 = cached_app.get("/dashboard")
    assert r1.status_code == 200
    assert r1.headers.get("hit_cache") is None  # first hit - real render, not cached yet

    r2 = cached_app.get("/dashboard")
    assert r2.status_code == 200
    assert r2.headers.get("hit_cache") == "True"  # second hit - served from cache


def test_differently_filtered_jobs_request_is_not_served_from_unfiltered_cache(cached_app):
    r1 = cached_app.get("/jobs")
    r2 = cached_app.get("/jobs?market=ai_ml_global")
    assert r1.status_code == 200 and r2.status_code == 200
    # Different query string -> must NOT be a cache hit against the
    # unfiltered /jobs response from r1, even though both hit the same
    # view function moments apart.
    assert r2.headers.get("hit_cache") is None
