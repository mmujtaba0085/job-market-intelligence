"""
tests/test_notifications_rendering.py
────────────────────────────────────────
End-to-end (via Flask test client) proof that an admin-created notification
actually appears on a targeted page's rendered HTML, does not appear on an
untargeted page, and is excluded once its id is in the jmi_dismissed cookie.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    # Deliberately NOT a hand-rolled jobs/skills schema (unlike
    # test_public_viewable_routes.py's otherwise-identical fixture): this
    # fixture calls db.run_migrations() below (needed to create the
    # notifications table in the isolated operational DB), and that applies
    # 001_init.sql's real CREATE TABLE/CREATE INDEX statements plus several
    # later migrations' Python-special-cased ALTER/UPDATE steps that assume
    # a full legacy jobs table (canonical_hash, salary_min, first_seen_at,
    # ...). CREATE TABLE IF NOT EXISTS is a no-op once a same-named table
    # already exists, so a hand-rolled subset schema left those later
    # statements failing with "no such column" (found by actually running
    # this fixture, one missing column at a time). Simplest robust fix:
    # let run_migrations() build the real, complete schema from scratch -
    # matching the isolated_paths fixture in tests/test_notifications.py.
    # None of this file's tests need seeded job rows (only notification-bar
    # text), so an empty-but-existing file is enough. It still needs to
    # *exist* before run_migrations() runs, though: db._bootstrap_rotation_files()
    # backs up the real (unpatched) src.storage.db.DB_PATH into any
    # serving/buffer path that doesn't already exist, and in this dev
    # environment data/jobs.sqlite is a real ~58MB file - an absent
    # db_path here would make every test run copy it into our "isolated"
    # DB instead of starting genuinely empty.
    sqlite3.connect(str(db_path)).close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    # Same reasoning as db_path above: must exist before run_migrations()
    # runs, or _bootstrap_rotation_files() backs up the real (unpatched)
    # DB_PATH into it instead of starting genuinely empty.
    operational_db_path = tmp_path / "operational.sqlite"
    sqlite3.connect(str(operational_db_path)).close()
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", operational_db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    import src.storage.db as db
    db.run_migrations()

    return web_viewer.app.test_client()


def _seed_notification(heading="Maintenance tonight", target_pages="all", expires_at=None):
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (heading, "Details here.", "warning", target_pages, datetime.now(timezone.utc).isoformat(), expires_at),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", (heading,)).fetchone()[0]
    conn.close()
    return row_id


def test_all_pages_notification_appears_on_jobs_page(anon_client):
    _seed_notification(target_pages="all")
    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b"Maintenance tonight" in resp.data


def test_page_specific_notification_does_not_appear_on_untargeted_page(anon_client):
    _seed_notification(heading="Jobs-only notice", target_pages="jobs")
    resp = anon_client.get("/dashboard")
    assert b"Jobs-only notice" not in resp.data


def test_page_specific_notification_appears_on_targeted_page(anon_client):
    _seed_notification(heading="Jobs-only notice", target_pages="jobs")
    resp = anon_client.get("/jobs")
    assert b"Jobs-only notice" in resp.data


def test_dismissed_notification_does_not_appear(anon_client):
    row_id = _seed_notification(heading="Dismiss me")
    anon_client.set_cookie("jmi_dismissed", str(row_id))
    resp = anon_client.get("/jobs")
    assert b"Dismiss me" not in resp.data


def test_notification_without_dismiss_cookie_appears(anon_client):
    _seed_notification(heading="Not dismissed")
    resp = anon_client.get("/jobs")
    assert b"Not dismissed" in resp.data


def test_no_active_notifications_renders_no_notification_bar_markup(anon_client):
    """base.html must render cleanly for the common case (no active
    notifications) - no stray empty .notification-bar div, no broken
    layout, and the surrounding page chrome must still be present.

    Checks for 'class="notification-bar' specifically, with the HTML
    attribute-syntax prefix. Two broader substrings were tried and both
    turned out to be false positives that are always present regardless of
    notification state, confirmed empirically rather than assumed:
      - "notification-bar" alone also matches base.html's static <style>
        block, which defines the .notification-bar CSS selector on every
        page.
      - "data-notification-id" alone also matches the literal JS source of
        dismissNotification() in base.html's <script>
        (document.querySelector('[data-notification-id="' + id + '"]')),
        present on every page whether or not any notification is active.
    'class="notification-bar' (the opening HTML tag syntax) appears
    exactly once across base.html + _notifications.html: inside
    _notifications.html's {% for %} loop body, which only emits it per
    actual active notification."""
    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b'class="notification-bar' not in resp.data
    # sanity: this is still a real, fully-rendered page, not an error page
    assert b"<header>" in resp.data
    assert b"<footer>" in resp.data


def test_past_expiry_notification_does_not_appear_and_page_still_renders(anon_client):
    """Regression guard for the naive-vs-aware datetime bug flagged in Task
    1's review: load_active_notifications()'s expiry check does
    `now >= expires_at`, and raises TypeError if `now` is naive while
    `expires_at` (stored here as a tz-aware isoformat string) is aware. If
    the before_request hook ever regresses to a naive `datetime.now()`,
    this request raises mid-request instead of returning 200 (Flask
    TESTING=True propagates the exception through the test client rather
    than masking it as a 500 page)."""
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=1)).isoformat()
    _seed_notification(heading="Expired notice", target_pages="all", expires_at=past)
    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b"Expired notice" not in resp.data


def test_future_expiry_notification_still_appears(anon_client):
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    _seed_notification(heading="Still active notice", target_pages="all", expires_at=future)
    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b"Still active notice" in resp.data


def test_missing_notifications_table_degrades_to_no_notifications(anon_client):
    """A real regression this task's implementation hit and fixed: many
    pre-existing test fixtures elsewhere in this repo (e.g.
    test_public_viewable_routes.py) build their own isolated, hand-rolled
    operational DB without ever calling run_migrations(), so it has no
    notifications table. Wiring this hook in globally made every one of
    those page requests raise sqlite3.OperationalError mid-request (31
    failures across 8 unrelated test files when the full suite was run) -
    an optional announcement bar must never turn every page on the site
    into a 500. Simulated directly here by dropping the table that
    anon_client's own run_migrations() call just created, then confirming
    the page still renders normally with simply no notifications shown."""
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute("DROP TABLE notifications")
    conn.commit()
    conn.close()

    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b'class="notification-bar' not in resp.data


def test_healthz_and_static_are_excluded_from_notification_lookup(anon_client, monkeypatch):
    """_load_active_notifications() must short-circuit for /healthz and
    /static/* exactly like the pre-existing _track_last_request_at() hook
    does, rather than hitting the operational DB on every asset request."""
    import src.notifications as notifications_module

    calls = []
    original = notifications_module.load_active_notifications

    def _spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(notifications_module, "load_active_notifications", _spy)

    anon_client.get("/healthz")
    assert calls == []
