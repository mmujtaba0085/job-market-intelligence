import sqlite3

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, company TEXT, listing_status TEXT,
            raw_description TEXT, field_category_id TEXT, field_classification_confidence REAL,
            field_classification_method TEXT, field_classification_attempted_at TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    conn.execute("INSERT INTO jobs (job_id, title, company, listing_status, field_category_id) VALUES (1, 'Dev', 'Co', 'active', 'it.software')")
    conn.execute("INSERT INTO jobs (job_id, title, company, listing_status) VALUES (2, 'Mystery', 'Co', 'active')")
    # parent_id must be non-NULL for this row to satisfy the dashboard's
    # category-breakdown query (`WHERE jc.parent_id IS NOT NULL`), which
    # deliberately excludes top-level/root taxonomy nodes.
    conn.execute("INSERT INTO job_categories (category_id, name, parent_id) VALUES ('it.software', 'Software Engineering', 'it')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, prompt_sent, response_received, created_at) VALUES (2, 'pending', 'p', NULL, datetime('now'))")
    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at, jobs_processed, jobs_classified) VALUES ('r1', 'local_incremental', 'schedule', 'success', datetime('now'), datetime('now'), 2, 1)")
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    # The admin action routes (run-local, groq-backlog/run-now, full-reclassify/*,
    # queue delete, config) go through src.storage.db.get_connection() and
    # src.pipeline_monitor.get_config()/set_config() rather than web_viewer's own
    # get_db_connection() - both need to be redirected to the isolated tmp db, or
    # they'd silently read/write the real dev database (see the same pattern in
    # tests/test_classification_scheduling.py's `conn` fixture).
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


def test_dashboard_requires_admin(tmp_path, monkeypatch):
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

    r = client.get("/admin/classification", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_dashboard_shows_run_history_and_category_breakdown(admin_client):
    r = admin_client.get("/admin/classification")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "local_incremental" in html
    assert "Software Engineering" in html


def test_dashboard_renders_config_form_with_current_values(admin_client):
    r = admin_client.get("/admin/classification")
    html = r.get_data(as_text=True)
    assert 'name="classification_confidence_threshold"' in html
    assert 'name="classification_idle_seconds"' in html
    assert 'name="classification_retry_cap"' in html
    assert 'name="classification_local_chunk_size"' in html
    assert 'name="classification_groq_chunk_size"' in html


def test_run_local_classification(admin_client):
    r = admin_client.post("/admin/classification/run-local", data={"csrf_token": "test-csrf"})
    assert r.status_code == 200
    data = r.get_json()
    assert "run_id" in data


def test_delete_queue_row(admin_client):
    r = admin_client.post("/admin/classification/queue/1/delete", data={"csrf_token": "test-csrf"})
    assert r.status_code == 200

    # Precise check tied to the template's per-row id="queue-row-{{ q.id }}"
    # attribute (used by the delete button's JS target), not a generic
    # "pending"/"0" text heuristic that could pass even if deletion silently
    # failed (those substrings can legitimately appear elsewhere on the page,
    # e.g. a "Never attempted: 0" stat tile).
    r2 = admin_client.get("/admin/classification")
    assert 'id="queue-row-1"' not in r2.get_data(as_text=True)


def test_full_reclassify_confirm_starts_local_full_backfill_run(admin_client):
    r = admin_client.post("/admin/classification/full-reclassify/confirm", data={"csrf_token": "test-csrf"})
    assert r.status_code == 200
    data = r.get_json()
    assert "run_id" in data

    from src.storage.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT run_type, status FROM classification_runs WHERE run_id = ?", (data["run_id"],)
    ).fetchone()
    conn.close()
    assert row["run_type"] == "local_full_backfill"
    assert row["status"] == "running"
