"""
tests/test_admin_notifications_routes.py
─────────────────────────────────────────
Admin-facing create/list/remove routes for the notifications management
page (Task 3). Uses the real run_migrations() bootstrap across the rotating
DB paths (not a hand-rolled schema) since the notifications table lives in
operational.sqlite alongside pipeline_config/pipeline_runs, and is created
by db.py's Python-special-cased CREATE TABLE step - only the real migration
path builds that correctly.

Every monkeypatched DB path that run_migrations() can write to
(_SERVING_A_PATH, _SERVING_B_PATH, _BUFFER_DB_PATH, _OPERATIONAL_DB_PATH) is
pre-created as an empty file BEFORE db.run_migrations() runs - see
tests/test_notifications_rendering.py's anon_client fixture for the same
pattern. Without this, db._bootstrap_rotation_files() sees a target path
that doesn't exist yet and falls through to backing up the REAL, unpatched
config.settings.DB_PATH (a real, large local dev database) into it instead
of starting genuinely empty. This was a real bug hit during Task 2's review
cycle (not hypothetical), so every path in that four-tuple is pre-created
here via sqlite3.connect(str(path)).close(), matching the sibling paths
that were already safe in the original buggy fixture.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _pre_create_rotation_db_files(db) -> None:
    """Must be called AFTER the monkeypatch.setattr() calls that redirect
    these five path constants into tmp_path, and BEFORE db.run_migrations()
    - see module docstring for why. _POINTER_PATH pre-created empty is inert
    (an empty pointer file falls back to "a" in _read_pointer(), same as a
    missing one) but included for literal parity with the other four."""
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

    import src.auth.models as models
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
    r = client.get("/admin/notifications", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_dashboard_renders(admin_client):
    r = admin_client.get("/admin/notifications")
    assert r.status_code == 200


def test_create_inserts_a_row(admin_client):
    r = admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Scheduled maintenance",
        "body": "The site will be briefly unavailable at 2am UTC.",
        "severity": "warning",
        "target_pages": "all",
    })
    assert r.status_code in (200, 302)

    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT heading, severity, target_pages, removed_at FROM notifications").fetchone()
    conn.close()
    assert row["heading"] == "Scheduled maintenance"
    assert row["severity"] == "warning"
    assert row["target_pages"] == "all"
    assert row["removed_at"] is None


def test_create_with_specific_pages_joins_them_with_commas(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Jobs page notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "jobs",
        "pages": ["jobs", "dashboard"],
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT target_pages FROM notifications WHERE heading = ?", ("Jobs page notice",)).fetchone()
    conn.close()
    assert set(row["target_pages"].split(",")) == {"jobs", "dashboard"}


def test_create_with_expiry_hours_sets_absolute_expires_at(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Temporary notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "all",
        "expires_in_hours": "2",
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT expires_at FROM notifications WHERE heading = ?", ("Temporary notice",)).fetchone()
    conn.close()
    assert row["expires_at"] is not None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now + timedelta(hours=1, minutes=55) < expires_at < now + timedelta(hours=2, minutes=5)


def test_create_without_expiry_leaves_expires_at_null(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Permanent-ish notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "all",
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT expires_at FROM notifications WHERE heading = ?", ("Permanent-ish notice",)).fetchone()
    conn.close()
    assert row["expires_at"] is None


def test_remove_sets_removed_at(admin_client):
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("To be removed", "Body.", "info", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", ("To be removed",)).fetchone()["id"]
    conn.close()

    r = admin_client.post(f"/admin/notifications/{row_id}/remove", data={"csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    conn = get_operational_connection()
    row = conn.execute("SELECT removed_at FROM notifications WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    assert row["removed_at"] is not None


def test_removed_notification_stops_appearing_in_active_list(admin_client):
    from src.notifications import load_active_notifications
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("Active then removed", "Body.", "info", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", ("Active then removed",)).fetchone()["id"]
    conn.close()

    admin_client.post(f"/admin/notifications/{row_id}/remove", data={"csrf_token": "test-csrf"})

    result = load_active_notifications("/jobs", set(), datetime.now(timezone.utc))
    assert row_id not in {r["id"] for r in result}
