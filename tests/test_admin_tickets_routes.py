"""
tests/test_admin_tickets_routes.py
──────────────────────────────────────
Regression coverage for /admin/tickets - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.

Uses the real run_migrations() bootstrap across the rotating DB paths,
matching tests/test_admin_reports_routes.py's proven fixture exactly
(pre-created empty rotation files before run_migrations(), real admin
user lookup) - see that file's docstring for why the naive alternative
(hand-rolled schema + run_migrations()) is a known-bad combination.
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

    from src.tickets import create_ticket
    op_conn = db.get_operational_connection()
    ticket_id = create_ticket(
        op_conn, category="feature", subject="Add LinkedIn as a source",
        details="Would be great to include LinkedIn postings", submitter_user_id=None,
        submitter_email=None, submitter_ip="1.2.3.4", now=datetime.now(timezone.utc),
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
    client._seeded_ticket_id = ticket_id
    return client


def test_tickets_page_requires_admin(tmp_path, monkeypatch):
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
    r = client.get("/admin/tickets", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_tickets_page_lists_open_tickets_by_default(admin_client):
    r = admin_client.get("/admin/tickets")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Add LinkedIn as a source" in body


def test_tickets_page_status_all_shows_everything(admin_client):
    import src.storage.db as db
    from src.tickets import create_ticket
    op_conn = db.get_operational_connection()
    dismissed_id = create_ticket(
        op_conn, category="bug", subject="Dismissed bug report", details="Not reproducible",
        submitter_user_id=None, submitter_email=None, submitter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE tickets SET status = 'dismissed' WHERE ticket_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/tickets")
    assert "Dismissed bug report" not in r.get_data(as_text=True)

    r_all = admin_client.get("/admin/tickets?status=all")
    body_all = r_all.get_data(as_text=True)
    assert "Dismissed bug report" in body_all
    assert "Add LinkedIn as a source" in body_all


def test_mark_in_progress_updates_status(admin_client):
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/in-progress", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, resolved_at FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "in_progress"
    assert row["resolved_at"] is None  # non-terminal, not resolved


def test_resolve_ticket_updates_status(admin_client):
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/resolve", data={"_csrf_token": "test-csrf", "admin_notes": "Added in v2"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, admin_notes FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
    assert row["admin_notes"] == "Added in v2"


def test_dismiss_ticket_updates_status(admin_client):
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/dismiss", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "dismissed"


def test_in_progress_ticket_can_still_be_resolved(admin_client):
    ticket_id = admin_client._seeded_ticket_id
    admin_client.post(f"/admin/tickets/{ticket_id}/in-progress", data={"_csrf_token": "test-csrf"})
    r = admin_client.post(f"/admin/tickets/{ticket_id}/resolve", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
