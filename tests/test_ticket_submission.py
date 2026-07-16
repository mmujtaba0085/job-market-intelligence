"""
tests/test_ticket_submission.py
───────────────────────────────────
End-to-end coverage for POST /tickets - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def ticket_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (job_id INTEGER PRIMARY KEY, title TEXT, listing_status TEXT DEFAULT 'active')")
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.commit()
    conn.close()

    operational_db_path = tmp_path / "operational.sqlite"
    op_conn = sqlite3.connect(str(operational_db_path))
    op_conn.execute("""
        CREATE TABLE tickets (
            ticket_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            category          TEXT NOT NULL,
            subject           TEXT NOT NULL,
            details           TEXT NOT NULL,
            submitter_user_id INTEGER,
            submitter_email   TEXT,
            submitter_ip      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'open',
            admin_notes       TEXT,
            created_at        TEXT NOT NULL,
            resolved_at       TEXT
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


def test_anonymous_visitor_can_submit_a_ticket(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "feature", "subject": "Add LinkedIn as a source", "details": "Would be great to include LinkedIn postings"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 200
    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT * FROM tickets WHERE subject = 'Add LinkedIn as a source'").fetchone()
    conn.close()
    assert row is not None
    assert row["submitter_user_id"] is None
    assert row["category"] == "feature"


def test_missing_csrf_token_is_rejected(ticket_client):
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "Subject", "details": "Details"},
    )
    assert r.status_code == 400


def test_blank_details_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "Subject", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_blank_subject_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "", "details": "Some details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_unrecognized_category_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "not_a_category", "subject": "Subject", "details": "Details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_sixth_ticket_from_same_ip_within_an_hour_is_rate_limited(ticket_client):
    _csrf_client(ticket_client)
    for i in range(5):
        r = ticket_client.post(
            "/tickets",
            data={"category": "feedback", "subject": f"Subject {i}", "details": "Details"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert r.status_code == 200
    r = ticket_client.post(
        "/tickets",
        data={"category": "feedback", "subject": "One too many", "details": "Details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 429
