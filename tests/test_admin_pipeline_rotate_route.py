"""
tests/test_admin_pipeline_rotate_route.py
──────────────────────────────────────────
Tests for the "Rotate Now" admin route (/admin/pipeline/rotate) and the
rotation_max_interval_hours config field added to /admin/pipeline/config.

Uses the same admin_client fixture pattern as
tests/test_admin_classification_routes.py (single-file DB_PATH/rotation-path
monkeypatch emulation - not the "genuinely separate physical files" variant,
since these routes don't need to distinguish Free from Serving).
"""
import sqlite3

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, company TEXT, listing_status TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE pipeline_runs (
            run_id TEXT PRIMARY KEY, mode TEXT, status TEXT, trigger TEXT,
            started_at TEXT, finished_at TEXT, duration_seconds INTEGER,
            jobs_fetched INTEGER, jobs_inserted INTEGER, jobs_deduped INTEGER,
            skills_extracted INTEGER, error TEXT
        );
    """)
    conn.execute(
        "INSERT INTO pipeline_config (key, value, updated_at) VALUES "
        "('ingest_interval_hours', '12', datetime('now')), "
        "('crawl_interval_hours', '4', datetime('now')), "
        "('crawl_max_runtime_minutes', '30', datetime('now')), "
        "('weekly_day', 'Sunday', datetime('now')), "
        "('weekly_time', '03:00', datetime('now')), "
        "('rotation_max_interval_hours', '12', datetime('now'))"
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    # Same reasoning as test_admin_classification_routes.py's admin_client:
    # the pipeline routes go through src.storage.db.get_operational_connection()
    # rather than web_viewer's own get_db_connection(), so all rotation-aware
    # DB path globals must be redirected to the isolated tmp db together -
    # patching only DB_PATH is a known-broken pattern in this codebase.
    monkeypatch.setattr("src.storage.db.DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    import src.auth.models as models
    from pathlib import Path
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    admin_id = next(u["id"] for u in models.list_users() if u["username"] == "admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_csrf_token"] = "test-csrf"
    return client


def test_rotate_now_route_calls_rotate_and_returns_result(admin_client, monkeypatch):
    from unittest.mock import Mock
    mock_rotate = Mock(return_value={"merged": 3, "rotated": True, "new_serving": "b"})
    monkeypatch.setattr("src.db_rotation.rotate", mock_rotate)

    resp = admin_client.post("/admin/pipeline/rotate", data={"csrf_token": "test-csrf"})

    assert resp.status_code == 200
    assert resp.get_json() == {"merged": 3, "rotated": True, "new_serving": "b"}
    mock_rotate.assert_called_once_with()


def test_rotate_now_route_requires_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (job_id INTEGER PRIMARY KEY, listing_status TEXT)")
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()

    resp = client.post("/admin/pipeline/rotate", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_pipeline_config_accepts_rotation_max_interval_hours(admin_client):
    resp = admin_client.post(
        "/admin/pipeline/config",
        data={"rotation_max_interval_hours": "6", "csrf_token": "test-csrf"},
    )
    assert resp.status_code == 200
    assert "rotation_max_interval_hours" in resp.get_json()["updated"]

    from src.pipeline_monitor import get_config
    assert get_config()["rotation_max_interval_hours"] == "6"
