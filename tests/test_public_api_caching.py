"""
tests/test_public_api_caching.py
───────────────────────────────────
Confirms one of the 8 newly-cached public API endpoints is actually
served from cache on a repeat request (via Flask-Caching's
response_hit_indication mechanism, same verified pattern as
tests/test_route_caching.py), and that an anonymous request and a
signed-in request to the same endpoint don't share a cache entry.
"""
import sqlite3

import pytest


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            listing_status TEXT, company TEXT,
            posted_date TEXT, first_seen_at TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
    """)
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
    return web_viewer.app.test_client()


def test_anonymous_repeat_request_is_served_from_cache(app_client):
    r1 = app_client.get("/api/dashboard/companies")
    assert r1.status_code == 200
    assert r1.headers.get("hit_cache") is None

    r2 = app_client.get("/api/dashboard/companies")
    assert r2.status_code == 200
    assert r2.headers.get("hit_cache") == "True"


def test_anonymous_and_signed_in_requests_dont_share_a_cache_entry(app_client):
    app_client.get("/api/dashboard/companies")  # anonymous, populates the anon cache entry

    with app_client.session_transaction() as sess:
        sess["user_id"] = 1
    r = app_client.get("/api/dashboard/companies")  # now signed in, same URL
    assert r.status_code == 200
    assert r.headers.get("hit_cache") is None, "signed-in request must not be served the anonymous visitor's cached response"
