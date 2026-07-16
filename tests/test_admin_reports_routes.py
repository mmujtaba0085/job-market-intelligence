"""
tests/test_admin_reports_routes.py
──────────────────────────────────────
Regression coverage for /admin/reports - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.

Uses the real run_migrations() bootstrap across the rotating DB paths
(not a hand-rolled schema) since job_reports lives in operational.sqlite
alongside notifications/pipeline_config, created by db.py's migration
step - only the real migration path builds that correctly. Every
monkeypatched DB path run_migrations() can write to is pre-created as an
empty file BEFORE db.run_migrations() runs, matching
tests/test_admin_notifications_routes.py's proven fixture exactly
(without this, db._bootstrap_rotation_files() sees a target path that
doesn't exist yet and falls through to backing up the real, unpatched
config.settings.DB_PATH into it instead of starting genuinely empty).
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _pre_create_rotation_db_files(db) -> None:
    for path in (db._SERVING_A_PATH, db._SERVING_B_PATH, db._BUFFER_DB_PATH, db._OPERATIONAL_DB_PATH):
        sqlite3.connect(str(path)).close()
    db._POINTER_PATH.touch()


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    import src.storage.db as db

    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    _pre_create_rotation_db_files(db)
    db.run_migrations()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", tmp_path / "serving_a.sqlite")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    from src.job_reports import create_report
    op_conn = db.get_operational_connection()
    report_id = create_report(
        op_conn, job_id=1, job_url="https://example.com/1", job_title="Backend Engineer",
        reason_category="incorrect_info", details="Salary wrong", reporter_user_id=None,
        reporter_email=None, reporter_ip="1.2.3.4", now=datetime.now(timezone.utc),
    )
    op_conn.close()

    import src.auth.models as models
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    admin_id = next(u["id"] for u in models.list_users() if u["username"] == "admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_csrf_token"] = "test-csrf"
    client._seeded_report_id = report_id
    return client


def test_reports_page_requires_admin(tmp_path, monkeypatch):
    import src.storage.db as db
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    _pre_create_rotation_db_files(db)
    db.run_migrations()

    import web_viewer
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()
    r = client.get("/admin/reports", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_reports_page_lists_open_reports(admin_client):
    r = admin_client.get("/admin/reports")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body
    assert "Salary wrong" in body


def test_reports_page_defaults_to_open_only(admin_client):
    import src.storage.db as db
    from src.job_reports import create_report
    op_conn = db.get_operational_connection()
    dismissed_id = create_report(
        op_conn, job_id=2, job_url="https://example.com/2", job_title="Frontend Engineer",
        reason_category="spam", details="", reporter_user_id=None,
        reporter_email=None, reporter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE job_reports SET status = 'dismissed' WHERE report_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/reports")
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body  # the open, seeded report
    assert "Frontend Engineer" not in body  # dismissed, excluded by default


def test_reports_page_status_all_shows_everything(admin_client):
    import src.storage.db as db
    from src.job_reports import create_report
    op_conn = db.get_operational_connection()
    dismissed_id = create_report(
        op_conn, job_id=2, job_url="https://example.com/2", job_title="Frontend Engineer",
        reason_category="spam", details="", reporter_user_id=None,
        reporter_email=None, reporter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE job_reports SET status = 'dismissed' WHERE report_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/reports?status=all")
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body
    assert "Frontend Engineer" in body


def test_resolve_report_updates_status(admin_client):
    report_id = admin_client._seeded_report_id
    r = admin_client.post(f"/admin/reports/{report_id}/resolve", data={"_csrf_token": "test-csrf", "admin_notes": "Fixed the salary"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, admin_notes FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
    assert row["admin_notes"] == "Fixed the salary"


def test_dismiss_report_updates_status(admin_client):
    report_id = admin_client._seeded_report_id
    r = admin_client.post(f"/admin/reports/{report_id}/dismiss", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    conn.close()
    assert row["status"] == "dismissed"
