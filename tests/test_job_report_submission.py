"""
tests/test_job_report_submission.py
───────────────────────────────────────
End-to-end coverage for POST /jobs/<job_id>/report - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def report_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, url TEXT,
            listing_status TEXT DEFAULT 'active'
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("INSERT INTO jobs (job_id, title, url) VALUES (1, 'Backend Engineer', 'https://example.com/job/1')")
    conn.commit()
    conn.close()

    operational_db_path = tmp_path / "operational.sqlite"
    op_conn = sqlite3.connect(str(operational_db_path))
    op_conn.execute("""
        CREATE TABLE job_reports (
            report_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           INTEGER,
            job_url          TEXT NOT NULL,
            job_title        TEXT NOT NULL,
            reason_category  TEXT NOT NULL,
            details          TEXT,
            reporter_user_id INTEGER,
            reporter_email   TEXT,
            reporter_ip      TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            admin_notes      TEXT,
            created_at       TEXT NOT NULL,
            resolved_at      TEXT
        )
    """)
    op_conn.commit()
    op_conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", operational_db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return web_viewer.app.test_client()


def _csrf_client(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    return client


def test_anonymous_visitor_can_submit_a_report(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "incorrect_info", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 200
    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT * FROM job_reports WHERE job_id = 1").fetchone()
    conn.close()
    assert row is not None
    assert row["reporter_user_id"] is None
    assert row["job_title"] == "Backend Engineer"


def test_missing_csrf_token_is_rejected(report_client):
    r = report_client.post("/jobs/1/report", data={"reason_category": "incorrect_info", "details": ""})
    assert r.status_code == 400


def test_other_category_without_details_is_rejected(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "other", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_unknown_job_id_returns_404(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/9999/report",
        data={"reason_category": "spam", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 404


def test_sixth_report_from_same_ip_within_an_hour_is_rate_limited(report_client):
    _csrf_client(report_client)
    for _ in range(5):
        r = report_client.post(
            "/jobs/1/report",
            data={"reason_category": "spam", "details": ""},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert r.status_code == 200
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "spam", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 429
