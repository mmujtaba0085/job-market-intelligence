"""
tests/test_skill_combinations_endpoint.py
─────────────────────────────────────────
Confirms /api/skills/combinations is fully gated (anonymous requests
blocked, not given a reduced-row teaser) and that signed-in requests get
exactly 20 rows - the summary table itself stores up to 50 rows for
headroom, but the endpoint response should never return more than what's
actually displayed.

This endpoint (and the skills_intelligence page that calls it) was
originally public-with-a-teaser (5 rows for anonymous visitors) and was
later reverted to fully gated by explicit request - see
_PUBLIC_API_READS in web_viewer.py.
"""
import sqlite3

import pytest


@pytest.fixture()
def app_client_with_30_pairs(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    # get_db_connection() in web_viewer.py runs "SELECT 1 FROM active_jobs
    # LIMIT 1" as a health check before handing back any connection (see
    # web_viewer.py, unrelated to this task - predates it) - jobs/active_jobs
    # must exist even though skill_combinations() itself never queries them.
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            listing_status TEXT
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("""
        CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b TEXT, co_count INTEGER)
    """)
    conn.executemany(
        "INSERT INTO skill_combinations_summary VALUES (?,?,?)",
        [(f"skill_a_{i}", f"skill_b_{i}", 100 - i) for i in range(30)],
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
    return web_viewer.app.test_client()


def test_anonymous_request_is_blocked(app_client_with_30_pairs):
    r = app_client_with_30_pairs.get("/api/skills/combinations")
    assert r.status_code == 401


def test_signed_in_request_gets_exactly_twenty_rows(app_client_with_30_pairs):
    with app_client_with_30_pairs.session_transaction() as sess:
        sess["user_id"] = 1
    r = app_client_with_30_pairs.get("/api/skills/combinations")
    assert r.status_code == 200
    assert len(r.get_json()) == 20
